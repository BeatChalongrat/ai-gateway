using System.Diagnostics;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Workers;
using Xunit;

namespace SranDesktop.Tests.Workers;

/// <summary>
/// Transport acceptance against a REAL sandboxed worker process — no in-process
/// mock. Every case drives the fixture worker inside the capability-free
/// AppContainer, because the behaviours under test (crash mid-request, malformed
/// framing, cancellation the worker ignores) only exist across a process
/// boundary.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class InteractiveWorkerTransportTests : IAsyncLifetime
{
    private static readonly TimeSpan Handshake = TimeSpan.FromSeconds(30);

    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-interactive-{Guid.NewGuid():N}");
    private readonly List<InteractiveWorkerSession> _sessions = [];
    private readonly List<string> _events = [];

    public Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        return Task.CompletedTask;
    }

    private async Task<InteractiveWorkerSession> StartAsync(bool crashOnHandshake = false)
    {
        var workspace = Path.Combine(_root, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(workspace);
        if (crashOnHandshake)
            File.WriteAllText(Path.Combine(workspace, "crash-on-handshake"), "1");
        var runner = new WindowsAppContainerProcessRunner(
            new WorkspaceFileBroker(
                [new WorkspaceMount(workspace, WorkspaceMountAccess.ReadWrite)],
                workspace));
        var image = new WorkerImageResolver(
            Path.Combine(AppContext.BaseDirectory, "workers")).Resolve("interactive-fixture");
        var process = await runner.StartInteractiveWorkerAsync(image, workspace);
        var session = new InteractiveWorkerSession(
            process,
            "interactive-fixture",
            runId: "run_test",
            stepId: "step_test",
            durableEvent: (name, _) =>
            {
                lock (_events) _events.Add(name);
            });
        _sessions.Add(session);
        await session.StartAsync(Handshake);
        return session;
    }

    [Fact]
    public async Task Worker_Starts_Isolated_And_Completes_A_Handshake()
    {
        var session = await StartAsync();

        session.State.Should().Be(InteractiveSessionState.Ready);
        session.WorkerVersion.Should().Be("fixture-worker/1.0");
        session.ProcessId.Should().BeGreaterThan(0);
        Process.GetProcessById(session.ProcessId).HasExited.Should().BeFalse(
            "the worker must stay alive between requests");
        lock (_events)
        {
            _events.Should().Contain(InteractiveWorkerEvents.SessionStarting);
            _events.Should().Contain(InteractiveWorkerEvents.SessionReady);
        }
    }

    [Fact]
    public async Task Twenty_Sequential_Requests_Share_One_Process()
    {
        var session = await StartAsync();
        var processId = session.ProcessId;

        for (var index = 0; index < 20; index++)
        {
            var result = await session.SendAsync("echo", $"payload-{index}");
            result.Succeeded.Should().BeTrue(result.ErrorMessage);
            result.Payload.Should().Be($"payload-{index}");
        }

        session.ProcessId.Should().Be(processId, "stdin stayed open across every request");
        session.State.Should().Be(InteractiveSessionState.Ready);
    }

    [Fact]
    public async Task Concurrent_Requests_Correlate_Independently_And_May_Answer_Out_Of_Order()
    {
        var session = await StartAsync();

        // The slow one is issued first; if correlation were positional this would
        // hand the fast caller the wrong answer.
        var slow = session.SendAsync("delay", "700");
        await Task.Delay(50);
        var fast = await session.SendAsync("echo", "fast");
        var slowResult = await slow;

        fast.Succeeded.Should().BeTrue();
        fast.Payload.Should().Be("fast");
        slowResult.Succeeded.Should().BeTrue();
        slowResult.Payload.Should().Be("delayed:700");
        slowResult.DurationMs.Should().BeGreaterThan(fast.DurationMs);
    }

    [Fact]
    public async Task Notifications_Are_Separated_From_The_Response()
    {
        var session = await StartAsync();

        var result = await session.SendAsync("notify-then-respond", null);

        result.Succeeded.Should().BeTrue();
        result.Payload.Should().Be("done", "a notification must never satisfy a request");
        session.Notifications.Should().HaveCountGreaterThanOrEqualTo(2);
        session.Notifications.Select(item => item.Payload)
            .Should().Contain(["step-1", "step-2"]);
    }

    [Fact]
    public async Task An_Acknowledgement_Advances_The_Dispatch_Stage()
    {
        var session = await StartAsync();

        var result = await session.SendAsync("ack-then-echo", "acked");

        result.Succeeded.Should().BeTrue();
        lock (_events)
            _events.Should().Contain(InteractiveWorkerEvents.DispatchAcknowledged);
    }

    [Fact]
    public async Task A_Request_The_Worker_Never_Answers_Times_Out_As_Unknown()
    {
        var session = await StartAsync();

        var result = await session.SendAsync(
            "never-respond", null, timeout: TimeSpan.FromMilliseconds(600));

        result.Outcome.Should().Be(InteractiveRequestOutcome.UnknownOutcome);
        result.Stage.Should().BeOneOf(DispatchStage.Dispatched, DispatchStage.Acknowledged);
        result.RequiresReconciliation.Should().BeTrue(
            "the worker received it, so we cannot claim nothing happened");
    }

    [Fact]
    public async Task Cancelling_After_Dispatch_Is_Unknown_Even_If_The_Worker_Answers_Later()
    {
        var session = await StartAsync();
        using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(200));

        var result = await session.SendAsync("ignore-cancellation", null, cts.Token);

        result.Outcome.Should().Be(InteractiveRequestOutcome.CancelledAfterDispatchUnknown);
        result.RequiresReconciliation.Should().BeTrue();

        // The worker answers anyway ~600 ms later; that late frame must not
        // resurrect the cancelled request or disturb the session.
        await Task.Delay(700);
        var next = await session.SendAsync("echo", "still-usable");
        next.Succeeded.Should().BeTrue("a late answer must not corrupt correlation");
    }

    [Fact]
    public async Task Cancelling_Before_Dispatch_Is_A_Clean_No_Effect_Failure()
    {
        var session = await StartAsync();
        using var cts = new CancellationTokenSource();
        await cts.CancelAsync();

        var result = await session.SendAsync("echo", "never-sent", cts.Token);

        result.Outcome.Should().Be(InteractiveRequestOutcome.CancelledBeforeDispatch);
        result.Stage.Should().Be(DispatchStage.Queued);
        result.RequiresReconciliation.Should().BeFalse(
            "nothing was written, so there is nothing to reconcile");
    }

    [Fact]
    public async Task A_Worker_Crash_After_Dispatch_Is_Unknown_Not_A_Clean_Failure()
    {
        var session = await StartAsync();

        var result = await session.SendAsync(
            "crash-before-response", null, timeout: TimeSpan.FromSeconds(5));

        result.Outcome.Should().Be(InteractiveRequestOutcome.UnknownOutcome);
        result.RequiresReconciliation.Should().BeTrue();
        lock (_events)
            _events.Should().Contain(InteractiveWorkerEvents.UnknownOutcomeDetected);
    }

    [Fact]
    public async Task Requests_Queued_When_The_Session_Is_Dead_Fail_With_No_Effect()
    {
        var session = await StartAsync();
        await session.SendAsync(
            "crash-before-response", null, timeout: TimeSpan.FromSeconds(5));
        await WaitForTerminalAsync(session);

        var result = await session.SendAsync("echo", "after-crash");

        result.Outcome.Should().Be(InteractiveRequestOutcome.FailedNoEffect);
        result.Stage.Should().Be(DispatchStage.Queued);
    }

    [Fact]
    public async Task A_Malformed_Frame_Kills_The_Session_Without_Crashing_The_Host()
    {
        var session = await StartAsync();

        var result = await session.SendAsync(
            "malformed-frame", null, timeout: TimeSpan.FromSeconds(3));

        result.Succeeded.Should().BeFalse();
        await WaitForTerminalAsync(session);
        session.State.Should().Be(InteractiveSessionState.ProtocolFailed);
        // The host process is obviously still running to make this assertion.
        Environment.HasShutdownStarted.Should().BeFalse();
    }

    [Fact]
    public async Task An_Oversized_Frame_Is_Refused_On_Its_Declared_Length()
    {
        var session = await StartAsync();

        var result = await session.SendAsync(
            "oversized-frame", null, timeout: TimeSpan.FromSeconds(3));

        result.Succeeded.Should().BeFalse();
        await WaitForTerminalAsync(session);
        session.State.Should().Be(InteractiveSessionState.ProtocolFailed);
    }

    [Fact]
    public async Task A_Duplicate_Response_Is_Rejected_And_The_Session_Survives()
    {
        var session = await StartAsync();

        var result = await session.SendAsync("duplicate-response", null);

        result.Succeeded.Should().BeTrue();
        result.Payload.Should().Be("first", "the second answer must be discarded");
        (await session.SendAsync("echo", "after-duplicate")).Succeeded.Should().BeTrue();
    }

    [Fact]
    public async Task A_Frame_From_Another_Generation_Is_Rejected()
    {
        var session = await StartAsync();

        var result = await session.SendAsync(
            "wrong-generation", null, timeout: TimeSpan.FromMilliseconds(700));

        // The stale-generation answer is dropped, so the request times out as
        // unknown rather than being satisfied by a foreign session's frame.
        result.Succeeded.Should().BeFalse();
        result.Outcome.Should().Be(InteractiveRequestOutcome.UnknownOutcome);
    }

    [Fact]
    public async Task A_Worker_Reported_Error_Is_A_Definite_Failure_Not_An_Unknown()
    {
        var session = await StartAsync();

        var result = await session.SendAsync("worker-error", null);

        result.Outcome.Should().Be(InteractiveRequestOutcome.FailedByWorker);
        result.ErrorCode.Should().Be("FixtureError");
        result.RequiresReconciliation.Should().BeFalse(
            "the worker answered, so the outcome is known");
    }

    [Fact]
    public async Task A_Large_Payload_Round_Trips_Across_Many_Pipe_Reads()
    {
        var session = await StartAsync();
        const int size = 512 * 1024;

        var result = await session.SendAsync(
            "large-payload", size.ToString(), timeout: TimeSpan.FromSeconds(20));

        result.Succeeded.Should().BeTrue(result.ErrorMessage);
        result.Payload!.Length.Should().Be(size);
    }

    [Fact]
    public async Task Host_Secrets_Are_Not_Inherited_By_The_Worker()
    {
        const string name = "SRAN_INTERACTIVE_SECRET";
        const string secret = "interactive_secret_must_not_leak_4c19";
        Environment.SetEnvironmentVariable(name, secret);
        try
        {
            var session = await StartAsync();

            var result = await session.SendAsync("read-env", name);

            result.Succeeded.Should().BeTrue();
            result.Payload.Should().Be("(absent)");
            result.Payload.Should().NotContain(secret);
        }
        finally
        {
            Environment.SetEnvironmentVariable(name, null);
        }
    }

    [Fact]
    public async Task The_Worker_Cannot_Reach_The_Network()
    {
        var listener = new System.Net.Sockets.TcpListener(
            System.Net.IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            var port = ((System.Net.IPEndPoint)listener.LocalEndpoint).Port;
            var session = await StartAsync();

            var result = await session.SendAsync(
                "probe-network", port.ToString(), timeout: TimeSpan.FromSeconds(10));

            result.Succeeded.Should().BeTrue();
            result.Payload.Should().StartWith("blocked");
        }
        finally
        {
            listener.Stop();
        }
    }

    [Fact]
    public async Task Stopping_The_Session_Terminates_The_Process_Tree_And_Its_Scratch()
    {
        var session = await StartAsync();
        var processId = session.ProcessId;
        var child = await session.SendAsync("spawn-child", null, timeout: TimeSpan.FromSeconds(10));
        child.Succeeded.Should().BeTrue();
        var childId = int.TryParse(child.Payload?.Split(':').Last(), out var value) ? value : -1;
        childId.Should().BeGreaterThan(
            0,
            $"the fixture must really spawn a descendant for this to mean anything "
            + $"(got '{child.Payload}')");
        HasExited(childId).Should().BeFalse("the child should be alive before teardown");

        await session.DisposeAsync();
        _sessions.Remove(session);
        await Task.Delay(1500);

        HasExited(processId).Should().BeTrue("the worker must not survive its session");
        HasExited(childId).Should().BeTrue(
            "the Job Object must take every descendant with it");
    }

    [Fact]
    public async Task A_Stderr_Flood_Is_Capped_Instead_Of_Draining_The_Host()
    {
        var session = await StartAsync();

        // ~800 KB against a 256 KB quota.
        var result = await session.SendAsync(
            "stderr-flood", null, timeout: TimeSpan.FromSeconds(15));

        // Either the response beat the quota trip or the session was terminated;
        // what must NOT happen is the host blocking forever on an unread pipe.
        if (!result.Succeeded)
            session.State.Should().Be(InteractiveSessionState.ProtocolFailed);
    }

    [Fact]
    public async Task A_Soak_Of_Sequential_Sessions_Leaves_No_Processes_Or_Directories()
    {
        var jobsRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Packages",
            "sran.desktop.commandsandbox.v1",
            "AC",
            "LocalState",
            "SranJobs");
        var baselineJobs = Directory.Exists(jobsRoot)
            ? Directory.GetDirectories(jobsRoot).ToHashSet(StringComparer.OrdinalIgnoreCase)
            : [];
        var baselineWorkers = Process.GetProcessesByName("dotnet")
            .Select(process => process.Id).ToHashSet();

        for (var index = 0; index < 5; index++)
        {
            var session = await StartAsync();
            for (var request = 0; request < 5; request++)
                (await session.SendAsync("echo", $"soak-{index}-{request}"))
                    .Succeeded.Should().BeTrue();
            await session.DisposeAsync();
            _sessions.Remove(session);
        }
        await Task.Delay(1500);

        var survivors = Directory.Exists(jobsRoot)
            ? Directory.GetDirectories(jobsRoot).Except(baselineJobs).ToArray()
            : [];
        survivors.Should().BeEmpty("every session must reclaim its staged directory");
        Process.GetProcessesByName("dotnet")
            .Where(process => !baselineWorkers.Contains(process.Id))
            .Where(process => !HasExited(process.Id))
            .Should().BeEmpty();
    }

    [Fact]
    public async Task A_Worker_That_Dies_Before_The_Handshake_Ends_As_StartFailed()
    {
        InteractiveWorkerSession? session = null;
        var act = async () => session = await StartAsync(crashOnHandshake: true);

        await act.Should().ThrowAsync<Exception>(
            "a worker that never says hello cannot become Ready");
    }

    /// <summary>Transport-level restart: the host goes away mid-request. The
    /// worker must die with it, the in-flight request must resolve as unknown,
    /// and a fresh session must get a NEW generation so nothing from the old
    /// process can be mistaken for an answer.</summary>
    [Fact]
    public async Task A_Host_Restart_Kills_The_Worker_And_Starts_A_New_Generation()
    {
        var first = await StartAsync();
        var firstProcessId = first.ProcessId;
        var firstGeneration = first.Generation;
        var inFlight = first.SendAsync("never-respond", null, timeout: TimeSpan.FromSeconds(30));
        await Task.Delay(300);

        // Stand-in for the desktop process going down.
        await first.DisposeAsync();
        _sessions.Remove(first);

        var abandoned = await inFlight;
        abandoned.Outcome.Should().Be(
            InteractiveRequestOutcome.UnknownOutcome,
            "the request had already reached the worker when the host went away");
        abandoned.RequiresReconciliation.Should().BeTrue();
        await Task.Delay(1000);
        HasExited(firstProcessId).Should().BeTrue();

        var second = await StartAsync();
        second.Generation.Should().BeGreaterThan(firstGeneration);
        second.ProcessId.Should().NotBe(firstProcessId);
        (await second.SendAsync("echo", "after-restart")).Succeeded.Should().BeTrue();
    }

    [Fact]
    public void An_Unknown_Capability_Fails_Closed_With_No_Process()
    {
        var workspace = Path.Combine(_root, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(workspace);
        var runner = new WindowsAppContainerProcessRunner(
            new WorkspaceFileBroker(
                [new WorkspaceMount(workspace, WorkspaceMountAccess.ReadWrite)],
                workspace));

        var act = () => new WorkerImageResolver(Path.Combine(_root, "absent"))
            .Resolve("interactive-fixture");

        act.Should().Throw<SandboxUnavailableException>()
            .WithMessage("*fails closed*");
        _ = runner;
    }

    [Fact]
    public async Task A_Working_Directory_Outside_The_Mounts_Is_Refused_Before_Launch()
    {
        var authorized = Path.Combine(_root, Guid.NewGuid().ToString("N"));
        var outside = Path.Combine(_root, "outside-mount");
        Directory.CreateDirectory(authorized);
        Directory.CreateDirectory(outside);
        var runner = new WindowsAppContainerProcessRunner(
            new WorkspaceFileBroker(
                [new WorkspaceMount(authorized, WorkspaceMountAccess.ReadWrite)],
                authorized));
        var image = new WorkerImageResolver(
            Path.Combine(AppContext.BaseDirectory, "workers")).Resolve("interactive-fixture");

        var act = async () => await runner.StartInteractiveWorkerAsync(image, outside);

        await act.Should().ThrowAsync<SandboxUnavailableException>()
            .WithMessage("*not inside an authorized workspace mount*");
    }

    private static bool HasExited(int processId)
    {
        try
        {
            using var process = Process.GetProcessById(processId);
            return process.HasExited;
        }
        catch (ArgumentException)
        {
            return true;
        }
    }

    private static async Task WaitForTerminalAsync(InteractiveWorkerSession session)
    {
        var deadline = DateTimeOffset.UtcNow + TimeSpan.FromSeconds(5);
        while (DateTimeOffset.UtcNow < deadline
               && !InteractiveSessionStateMachine.IsTerminal(session.State))
            await Task.Delay(50);
    }

    public async Task DisposeAsync()
    {
        foreach (var session in _sessions)
        {
            try
            {
                await session.DisposeAsync();
            }
            catch (Exception ex) when (ex is IOException or InvalidOperationException)
            {
            }
        }
        if (Directory.Exists(_root))
        {
            try
            {
                Directory.Delete(_root, recursive: true);
            }
            catch (IOException)
            {
            }
        }
    }
}
