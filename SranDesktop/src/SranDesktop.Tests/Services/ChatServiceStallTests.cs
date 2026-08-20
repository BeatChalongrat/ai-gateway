using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Api;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Batch 2 T2 — stream idle watchdog (desktop half; mirrors web
/// chatStore.stall.test.ts). ChatService raises StreamStalledEvent(true)
/// when the frame stream is quiet past StreamIdleTimeout — heartbeat
/// frames (SSE ": ping" comments, surfaced by the reader as the ":"
/// sentinel) count as liveness, so a long legitimate tool run never
/// trips it — and clears with StreamStalledEvent(false) when data flows
/// again. The reader tests pin the comment-surfacing contract itself.
/// </summary>
public sealed class ChatServiceStallTests : IDisposable
{
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly TimeSpan _savedTimeout = ChatService.StreamIdleTimeout;

    public ChatServiceStallTests()
    {
        // Wide margins on purpose: the full suite runs these under heavy
        // parallel CPU load, where a Task.Delay can overshoot by tens of
        // milliseconds — thresholds must dwarf scheduler jitter.
        ChatService.StreamIdleTimeout = TimeSpan.FromMilliseconds(400);
    }

    public void Dispose() => ChatService.StreamIdleTimeout = _savedTimeout;

    private ChatService Create() => new(_api.Object, NullLogger<ChatService>.Instance);

    private static SseFrame Delta(string text) =>
        new("content_delta", $"{{\"delta\":\"{text}\"}}");

    private static SseFrame Complete() =>
        new("message_complete", "{\"message_id\":\"m1\"}");

    private void Streams(Func<CancellationToken, IAsyncEnumerable<SseFrame>> frames) =>
        _api.Setup(a => a.StreamMessageAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<IReadOnlyList<string>?>(),
                It.IsAny<bool>(), It.IsAny<bool>(), It.IsAny<CancellationToken>()))
            .Returns((string _, string _, IReadOnlyList<string>? _, bool _, bool _, CancellationToken ct)
                => frames(ct));

    private static async IAsyncEnumerable<SseFrame> QuietThenResumes()
    {
        yield return Delta("start");
        await Task.Delay(1600); // ≈4× the shortened threshold
        yield return Delta("back");
        yield return Complete();
    }

    private static async IAsyncEnumerable<SseFrame> HeartbeatsOnly()
    {
        yield return Delta("start");
        // ~1.5× the threshold in total wall-clock, but a heartbeat lands
        // every 50ms (8× headroom vs the 400ms threshold) — the watchdog
        // must never trip.
        for (var i = 0; i < 12; i++)
        {
            await Task.Delay(50);
            yield return SseStreamReader.HeartbeatFrame;
        }
        yield return Complete();
    }

    private static async IAsyncEnumerable<SseFrame> FastDeltas()
    {
        for (var i = 0; i < 20; i++)
        {
            await Task.Delay(1);
            yield return Delta($"t{i}");
        }
        yield return Complete();
    }

    private async Task<List<ChatStreamEvent>> RunTurnAsync()
    {
        var events = new List<ChatStreamEvent>();
        await foreach (var evt in Create().SendMessageAsync("c1", "hi"))
            events.Add(evt);
        return events;
    }

    [Fact]
    public async Task A_Quiet_Stream_Raises_Stalled_Then_Clears_When_Data_Resumes()
    {
        Streams(_ => QuietThenResumes());

        var events = await RunTurnAsync();

        var stalls = events.OfType<StreamStalledEvent>().ToList();
        stalls.Should().HaveCount(2);
        stalls[0].Stalled.Should().BeTrue();
        stalls[1].Stalled.Should().BeFalse();
        // Order: stall raised between the two deltas, cleared before "back".
        events.OfType<ContentDeltaEvent>().Select(d => d.Text)
            .Should().Equal("start", "back");
        events.IndexOf(stalls[0]).Should().BeGreaterThan(
            events.FindIndex(e => e is ContentDeltaEvent { Text: "start" }));
        events.IndexOf(stalls[1]).Should().BeLessThan(
            events.FindIndex(e => e is ContentDeltaEvent { Text: "back" }));
    }

    [Fact]
    public async Task Heartbeat_Frames_Keep_A_Long_Quiet_Tool_Run_Alive()
    {
        Streams(_ => HeartbeatsOnly());

        var events = await RunTurnAsync();

        events.OfType<StreamStalledEvent>().Should().BeEmpty();
        // Heartbeats are liveness only — they never map to events.
        events.Should().HaveCount(2); // delta + completed
    }

    [Fact]
    public async Task A_Fast_Stream_Never_Sees_The_Watchdog()
    {
        // Extra headroom: 1ms delays vs a 10s threshold — no plausible
        // scheduler stall bridges that under any suite load.
        ChatService.StreamIdleTimeout = TimeSpan.FromSeconds(10);
        Streams(_ => FastDeltas());

        var events = await RunTurnAsync();

        events.OfType<StreamStalledEvent>().Should().BeEmpty();
        events.OfType<ContentDeltaEvent>().Should().HaveCount(20);
    }
}

/// <summary>Reader-level contract for heartbeat surfacing (T2).</summary>
public sealed class SseStreamReaderHeartbeatTests
{
    private static Stream Bytes(string s) =>
        new MemoryStream(System.Text.Encoding.UTF8.GetBytes(s));

    private const string Wire =
        ": ping\n\nevent: content_delta\ndata: {\"delta\":\"x\"}\n\n: ping\n\n";

    [Fact]
    public async Task Comments_Stay_Invisible_By_Default()
    {
        var frames = new List<SseFrame>();
        await foreach (var f in SseStreamReader.ParseAsync(Bytes(Wire)))
            frames.Add(f);

        frames.Should().ContainSingle()
            .Which.Event.Should().Be("content_delta");
    }

    [Fact]
    public async Task Opted_In_Comments_Surface_As_The_Heartbeat_Sentinel()
    {
        var frames = new List<SseFrame>();
        await foreach (var f in SseStreamReader.ParseAsync(Bytes(Wire), yieldComments: true))
            frames.Add(f);

        frames.Should().HaveCount(3);
        frames[0].Should().Be(SseStreamReader.HeartbeatFrame);
        frames[1].Event.Should().Be("content_delta");
        frames[2].Should().Be(SseStreamReader.HeartbeatFrame);
    }
}
