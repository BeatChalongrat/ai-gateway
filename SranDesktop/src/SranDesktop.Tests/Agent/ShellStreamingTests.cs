using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Phase B PART 1: run_command streams output live via IProcessRunner,
/// and the bounded buffer keeps head+tail.</summary>
public class ShellStreamingTests
{
    private sealed class FakeSettings : ISettingsService
    {
        public UserSettings Current { get; } = new() { ShellTimeoutSeconds = 300 };
        public bool IsFirstRun => false;
        public event EventHandler? SettingsChanged;
        public void Save(UserSettings s) => SettingsChanged?.Invoke(this, EventArgs.Empty);
    }

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    private static RunCommandTool Tool(IProcessRunner runner) =>
        new(NullLogger<RunCommandTool>.Instance, new FakeSettings(), null, runner);

    // A runner that emits one line, then blocks until the test releases it —
    // lets the test prove the first chunk streamed WHILE the command was running.
    private sealed class GatedRunner : IProcessRunner
    {
        private readonly string[] _lines;
        public readonly TaskCompletionSource FirstLine = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public readonly TaskCompletionSource Release = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public int ExitCode;

        public GatedRunner(params string[] lines) => _lines = lines;

        public async Task<int> RunAsync(ProcessRunSpec spec, Action<string> onLine, CancellationToken ct)
        {
            onLine(_lines[0]);
            FirstLine.TrySetResult();
            await Release.Task.WaitAsync(ct);
            for (var i = 1; i < _lines.Length; i++)
                onLine(_lines[i]);
            return ExitCode;
        }
    }

    [Fact]
    public async Task Output_Streams_Incrementally_Before_The_Process_Exits()
    {
        var runner = new GatedRunner("line1", "line2", "line3");
        var tool = Tool(runner);
        var chunks = new List<string>();
        tool.OutputStreamed += c => { lock (chunks) chunks.Add(c.Text); };

        var exec = tool.ExecuteAsync(Args(new { command = "echo x" }), CancellationToken.None);

        // The first line arrived while the process is still running (not exited).
        await runner.FirstLine.Task.WaitAsync(TimeSpan.FromSeconds(5));
        exec.IsCompleted.Should().BeFalse();
        lock (chunks) chunks.Should().ContainSingle().Which.Should().Be("line1");

        runner.Release.SetResult();
        var result = await exec;

        // ≥2 progress updates streamed overall, and the final result still
        // carries the full output + exit code for the model.
        lock (chunks) chunks.Should().BeEquivalentTo(new[] { "line1", "line2", "line3" });
        result.Success.Should().BeTrue();
        result.Content.Should().Contain("line1").And.Contain("line3").And.Contain("[exit code 0]");
    }

    [Fact]
    public async Task NonZero_Exit_Is_Reported_In_Final_Result()
    {
        var runner = new GatedRunner("err: boom") { ExitCode = 2 };
        var tool = Tool(runner);
        var exec = tool.ExecuteAsync(Args(new { command = "build" }), CancellationToken.None);
        await runner.FirstLine.Task.WaitAsync(TimeSpan.FromSeconds(5));
        runner.Release.SetResult();

        var result = await exec;
        result.Content.Should().Contain("err: boom").And.Contain("[exit code 2]");
    }

    // A runner that emits partial output then simulates a timeout kill.
    private sealed class TimeoutRunner : IProcessRunner
    {
        public async Task<int> RunAsync(ProcessRunSpec spec, Action<string> onLine, CancellationToken ct)
        {
            onLine("partial output before kill");
            await Task.Yield();
            throw new OperationCanceledException(); // process killed at the timeout
        }
    }

    [Fact]
    public async Task Timeout_Still_Returns_Partial_Output_And_Resumable_Note()
    {
        // Outer ct NOT cancelled → the tool treats the cancel as a timeout and
        // returns the partial output + resumable note (Phase A behavior kept).
        var result = await Tool(new TimeoutRunner())
            .ExecuteAsync(Args(new { command = "long build" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("partial output before kill");
        result.Content.Should().Contain("still running");
    }

    [Fact]
    public async Task User_Cancellation_Propagates_Not_Treated_As_Timeout()
    {
        var runner = new GatedRunner("started");
        using var cts = new CancellationTokenSource();
        var exec = Tool(runner).ExecuteAsync(Args(new { command = "x" }), cts.Token);
        await runner.FirstLine.Task.WaitAsync(TimeSpan.FromSeconds(5));

        cts.Cancel();
        var act = async () => await exec;
        await act.Should().ThrowAsync<OperationCanceledException>();
    }

    // ── BoundedOutputBuffer ─────────────────────────────────────────────────

    [Fact]
    public void Bounded_Buffer_Keeps_Head_And_Tail_And_Drops_Middle()
    {
        var buf = new BoundedOutputBuffer(headLimit: 20, tailLimit: 20);
        for (var i = 0; i < 100; i++)
            buf.Append($"L{i}");

        var text = buf.ToString();
        text.Should().Contain("L0");          // head preserved
        text.Should().Contain("L99");         // tail preserved
        text.Should().Contain("truncated");   // middle dropped with a marker
        text.Should().NotContain("L50");      // a middle line is gone
    }

    [Fact]
    public void Bounded_Buffer_Small_Output_Is_Verbatim()
    {
        var buf = new BoundedOutputBuffer();
        buf.Append("hello");
        buf.Append("world");
        buf.ToString().Should().Be("hello\nworld");
    }
}
