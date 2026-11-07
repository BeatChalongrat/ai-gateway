using System.Net;
using System.Net.Http;
using System.Text;
using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 5 Task 3: the reattach retry gate — up to 4 attempts at 0/1s/2s/4s
/// backoff, target reset before EVERY attempt (each attempt re-delivers the
/// full prefix), single-flight per key, and terminal vs retryable
/// classification of server signals.
/// </summary>
public class StreamReattachRunnerTests
{
    private readonly List<TimeSpan> _delays = new();
    private readonly List<ChatStreamEvent> _forwarded = new();
    private int _resets;
    private readonly StreamReattachRunner _runner;

    public StreamReattachRunnerTests()
    {
        _runner = new StreamReattachRunner((delay, ct) =>
        {
            ct.ThrowIfCancellationRequested();
            _delays.Add(delay);
            return Task.CompletedTask;
        });
    }

    private Task<ReattachOutcome> RunAsync(
        Func<CancellationToken, IAsyncEnumerable<ChatStreamEvent>> attemptFactory,
        CancellationToken ct = default,
        string key = "conv-1") =>
        _runner.RunAsync(
            key,
            attemptFactory,
            resetTarget: () => _resets++,
            onEvent: evt => { _forwarded.Add(evt); return Task.CompletedTask; },
            ct);

    private static async IAsyncEnumerable<ChatStreamEvent> Stream(params ChatStreamEvent[] events)
    {
        foreach (var evt in events)
        {
            yield return evt;
            await Task.Yield();
        }
    }

    private static async IAsyncEnumerable<ChatStreamEvent> DeltaThenDrop(string text)
    {
        yield return new ContentDeltaEvent(text);
        await Task.Yield();
        throw new IOException("connection dropped");
    }

    private static HttpRequestException Http(HttpStatusCode code) =>
        new("server said no", null, code);

    [Fact]
    public async Task Completes_On_First_Attempt()
    {
        var outcome = await RunAsync(_ => Stream(
            new ContentDeltaEvent("prefix + live"),
            new StreamCompletedEvent(null)));

        outcome.Should().Be(ReattachOutcome.Completed);
        _resets.Should().Be(1, "the bubble is reset before every attempt, including the first");
        _delays.Should().Equal(TimeSpan.Zero);
        _forwarded.Should().Equal(
            new ContentDeltaEvent("prefix + live"),
            new StreamCompletedEvent(null));
    }

    [Fact]
    public async Task Retries_409_With_Backoff_Then_Succeeds()
    {
        var attempts = 0;
        var outcome = await RunAsync(_ =>
        {
            attempts++;
            if (attempts < 4)
                throw Http(HttpStatusCode.Conflict); // RESUME_UNAVAILABLE
            return Stream(new ContentDeltaEvent("finally"), new StreamCompletedEvent(null));
        });

        outcome.Should().Be(ReattachOutcome.Completed);
        attempts.Should().Be(4);
        _delays.Should().Equal(
            TimeSpan.Zero, TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(4));
        _resets.Should().Be(4);
    }

    [Fact]
    public async Task Gives_Up_After_Four_Failed_Attempts()
    {
        var attempts = 0;
        var outcome = await RunAsync(_ =>
        {
            attempts++;
            throw Http(HttpStatusCode.Conflict);
        });

        outcome.Should().Be(ReattachOutcome.Lost);
        attempts.Should().Be(4);
        _delays.Should().Equal(
            TimeSpan.Zero, TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(4));
    }

    [Fact]
    public async Task Reset_Before_Each_Attempt_Prevents_Duplicated_Text()
    {
        // A consumer bubble: deltas append, reset empties. The first attempt
        // drops mid-prefix; the retry re-delivers the FULL prefix — without
        // the reset the bubble would read "Hello worHello world".
        var bubble = new StringBuilder();
        var attempts = 0;
        var outcome = await _runner.RunAsync(
            "conv-1",
            _ => ++attempts == 1
                ? DeltaThenDrop("Hello wor")
                : Stream(new ContentDeltaEvent("Hello world"), new StreamCompletedEvent(null)),
            resetTarget: () => bubble.Clear(),
            onEvent: evt =>
            {
                if (evt is ContentDeltaEvent delta)
                    bubble.Append(delta.Text);
                return Task.CompletedTask;
            });

        outcome.Should().Be(ReattachOutcome.Completed);
        bubble.ToString().Should().Be("Hello world");
    }

    [Fact]
    public async Task Stream_Ending_Without_Completion_Retries()
    {
        var attempts = 0;
        var outcome = await RunAsync(_ => ++attempts == 1
            ? Stream(new ContentDeltaEvent("cut off"))
            : Stream(new ContentDeltaEvent("all of it"), new StreamCompletedEvent(null)));

        outcome.Should().Be(ReattachOutcome.Completed);
        attempts.Should().Be(2);
        _resets.Should().Be(2);
    }

    [Fact]
    public async Task Resume_Lagged_Reattaches_Fresh_Without_Forwarding_The_Control_Error()
    {
        var attempts = 0;
        var outcome = await RunAsync(_ => ++attempts == 1
            ? Stream(
                new ContentDeltaEvent("stale"),
                new StreamErrorEvent(StreamReattachRunner.CodeResumeLagged, "subscriber dropped"))
            : Stream(new ContentDeltaEvent("fresh"), new StreamCompletedEvent(null)));

        outcome.Should().Be(ReattachOutcome.Completed);
        attempts.Should().Be(2);
        _resets.Should().Be(2, "the lagged attempt's partial text must be reset away");
        _forwarded.OfType<StreamErrorEvent>().Should().BeEmpty("control errors never reach the bubble");
    }

    [Fact]
    public async Task Generation_Interrupted_Is_Terminal()
    {
        var attempts = 0;
        var outcome = await RunAsync(_ =>
        {
            attempts++;
            return Stream(
                new ContentDeltaEvent("partial"),
                new StreamErrorEvent(StreamReattachRunner.CodeGenerationInterrupted, "generation died"));
        });

        outcome.Should().Be(ReattachOutcome.Lost);
        attempts.Should().Be(1, "a dead generation must not be retried");
        _forwarded.OfType<StreamErrorEvent>().Should().BeEmpty();
    }

    [Fact]
    public async Task Not_Found_Is_Terminal()
    {
        var attempts = 0;
        var outcome = await RunAsync(_ =>
        {
            attempts++;
            throw Http(HttpStatusCode.NotFound);
        });

        outcome.Should().Be(ReattachOutcome.Lost);
        attempts.Should().Be(1, "the message/conversation is gone — retrying is pointless");
    }

    [Fact]
    public async Task Ordinary_Turn_Error_Is_Terminal_And_Forwarded()
    {
        var attempts = 0;
        var outcome = await RunAsync(_ =>
        {
            attempts++;
            return Stream(new StreamErrorEvent("provider_error", "rate limited"));
        });

        outcome.Should().Be(ReattachOutcome.Errored);
        attempts.Should().Be(1);
        _forwarded.Should().ContainSingle()
            .Which.Should().Be(new StreamErrorEvent("provider_error", "rate limited"));
    }

    [Fact]
    public async Task Cancellation_Stops_The_Run()
    {
        using var cts = new CancellationTokenSource();
        var outcome = await RunAsync(ct => Cancelling(cts, ct), cts.Token);

        outcome.Should().Be(ReattachOutcome.Cancelled);
    }

    private static async IAsyncEnumerable<ChatStreamEvent> Cancelling(
        CancellationTokenSource cts, [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken ct = default)
    {
        yield return new ContentDeltaEvent("some");
        cts.Cancel();
        await Task.Yield();
        ct.ThrowIfCancellationRequested();
    }

    [Fact]
    public async Task Single_Flight_Rejects_A_Second_Run_For_The_Same_Key_Only()
    {
        var gate = new TaskCompletionSource();
        var first = RunAsync(_ => Blocking(gate.Task), key: "conv-1");

        // Same key while the first run is still attached → rejected.
        (await RunAsync(_ => Stream(new StreamCompletedEvent(null)), key: "conv-1"))
            .Should().Be(ReattachOutcome.AlreadyRunning);

        // A different conversation is unaffected.
        (await RunAsync(_ => Stream(new StreamCompletedEvent(null)), key: "conv-2"))
            .Should().Be(ReattachOutcome.Completed);

        gate.SetResult();
        (await first).Should().Be(ReattachOutcome.Completed);

        // The key is released once the run ends.
        (await RunAsync(_ => Stream(new StreamCompletedEvent(null)), key: "conv-1"))
            .Should().Be(ReattachOutcome.Completed);
    }

    private static async IAsyncEnumerable<ChatStreamEvent> Blocking(Task gate)
    {
        await gate;
        yield return new StreamCompletedEvent(null);
    }
}
