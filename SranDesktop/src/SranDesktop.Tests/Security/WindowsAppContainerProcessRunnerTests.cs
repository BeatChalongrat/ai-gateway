using System.Net;
using System.Net.Sockets;
using System.Diagnostics;
using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Security;

[Collection("PathGuardGrants")]
public sealed class WindowsAppContainerProcessRunnerTests : IDisposable
{
    private readonly string _root;
    private readonly string _workspace;
    private readonly string _outside;
    private readonly WorkspaceFileBroker _broker;
    private readonly WindowsAppContainerProcessRunner _runner;

    public WindowsAppContainerProcessRunnerTests()
    {
        _root = Path.Combine(Path.GetTempPath(), $"sran-sandbox-{Guid.NewGuid():N}");
        _workspace = Path.Combine(_root, "workspace");
        _outside = Path.Combine(_root, "outside");
        Directory.CreateDirectory(_workspace);
        Directory.CreateDirectory(_outside);
        _broker = new WorkspaceFileBroker(
            [new WorkspaceMount(_workspace, WorkspaceMountAccess.ReadWrite)],
            _workspace);
        _runner = new WindowsAppContainerProcessRunner(_broker);
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task Safe_Command_Runs_And_Workspace_Change_Is_Reconciled()
    {
        var output = new List<string>();

        var exit = await RunCmdAsync(
            "echo sandbox-ok & echo file-ok>created.txt",
            output.Add,
            CancellationToken.None);

        exit.Should().Be(0);
        output.Should().Contain(line => line.Contains("sandbox-ok", StringComparison.Ordinal));
        (await File.ReadAllTextAsync(Path.Combine(_workspace, "created.txt")))
            .Trim().Should().Be("file-ok");
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task Host_Environment_Secret_Is_Not_Inherited()
    {
        const string name = "SRAN_SYNTHETIC_SECRET";
        const string secret = "sran_secret_must_not_enter_worker_7f0c";
        var previous = Environment.GetEnvironmentVariable(name);
        Environment.SetEnvironmentVariable(name, secret);
        try
        {
            var output = new List<string>();
            var exit = await RunCmdAsync($"set {name}", output.Add, CancellationToken.None);

            exit.Should().NotBe(0);
            string.Join("\n", output).Should().NotContain(secret);
        }
        finally
        {
            Environment.SetEnvironmentVariable(name, previous);
        }
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task File_Outside_Staged_Workspace_Cannot_Be_Read()
    {
        var outsideFile = Path.Combine(_outside, "secret.txt");
        const string secret = "outside-secret-34d91";
        await File.WriteAllTextAsync(outsideFile, secret);
        var output = new List<string>();

        var exit = await RunCmdAsync(
            $"type \"{outsideFile}\"",
            output.Add,
            CancellationToken.None);

        exit.Should().NotBe(0);
        string.Join("\n", output).Should().NotContain(secret);
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task AppContainer_Has_No_Loopback_Network_Capability()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            var port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var accept = listener.AcceptTcpClientAsync();
            var exit = await RunCmdAsync(
                $"curl.exe -sS --max-time 2 http://127.0.0.1:{port} >nul 2>&1",
                _ => { },
                CancellationToken.None);
            var connected = await Task.WhenAny(accept, Task.Delay(500)) == accept;

            connected.Should().BeFalse(
                "a capability-free AppContainer must not connect to loopback");
            exit.Should().NotBe(0);
        }
        finally
        {
            listener.Stop();
        }
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task Cancellation_Terminates_The_Supervised_Process()
    {
        using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(500));
        Func<Task> action = async () =>
        {
            await RunCmdAsync(
                "for /L %i in (1,1,2147483647) do @rem",
                _ => { },
                cts.Token);
        };

        await action.Should().ThrowAsync<OperationCanceledException>();
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task Detached_Child_Cannot_Outlive_Job()
    {
        var command =
            "start \"\" /b powershell.exe -NoLogo -NoProfile -NonInteractive " +
            "-Command \"Start-Sleep -Seconds 3; Set-Content -Path late.txt -Value child\" " +
            ">nul 2>&1 & echo parent";

        var exit = await RunCmdAsync(command, _ => { }, CancellationToken.None);
        await Task.Delay(TimeSpan.FromSeconds(4));

        exit.Should().Be(0);
        File.Exists(Path.Combine(_workspace, "late.txt")).Should().BeFalse();
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task Output_Quota_Terminates_Flooding_Process()
    {
        Func<Task> action = async () =>
        {
            await RunCmdAsync(
                "for /L %i in (1,1,300000) do @echo xxxxxxxxxx",
                _ => { },
                CancellationToken.None);
        };

        await action.Should().ThrowAsync<SandboxOutputLimitException>();
    }

    [Fact]
    public async Task Unapproved_Executable_Fails_Closed()
    {
        var spec = new ProcessRunSpec(
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "whoami.exe"),
            string.Empty,
            _workspace);
        Func<Task> action = async () =>
            await _runner.RunAsync(spec, _ => { }, CancellationToken.None);

        await action.Should().ThrowAsync<SandboxUnavailableException>();
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public void Windows_Job_Reports_Applied_Cpu_Memory_Process_And_Kill_Quotas()
    {
        var policy = WindowsAppContainerProcessRunner.InspectAppliedJobPolicy();

        (policy.CpuControlFlags & 0x00000001).Should().NotBe(0);
        (policy.CpuControlFlags & 0x00000004).Should().NotBe(0);
        policy.CpuRate.Should().Be(2_000);
        (policy.LimitFlags & 0x00002000).Should().NotBe(0);
        (policy.LimitFlags & 0x00000008).Should().NotBe(0);
        (policy.LimitFlags & 0x00000100).Should().NotBe(0);
        (policy.LimitFlags & 0x00000200).Should().NotBe(0);
        policy.ActiveProcessLimit.Should().Be(16);
        policy.ProcessMemoryLimit.Should().Be(512UL * 1024 * 1024);
        policy.JobMemoryLimit.Should().Be(768UL * 1024 * 1024);
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task Repeated_Sandbox_Runs_Do_Not_Leave_Command_Workers()
    {
        var baseline = Process.GetProcessesByName("cmd").Select(process => process.Id).ToHashSet();
        for (var index = 0; index < 10; index++)
        {
            var exit = await RunCmdAsync($"echo soak-{index}", _ => { }, CancellationToken.None);
            exit.Should().Be(0);
        }

        // "Leaves no workers" is a claim about where this settles, not about a
        // particular millisecond. A fixed delay measured it as though it were the
        // latter: a cmd that has been signalled and is still being torn down is
        // listed for a short window, and under full-suite CPU load that window
        // reaches past a flat 500 ms — which read as a leaked worker when nothing
        // had leaked. Waiting for the condition keeps the assertion exactly as
        // strong (the set must end up empty) and stops the machine's load from
        // deciding the verdict.
        var survivors = Array.Empty<int>();
        var deadline = DateTimeOffset.UtcNow + TimeSpan.FromSeconds(10);
        do
        {
            survivors = Process.GetProcessesByName("cmd")
                .Where(process => !baseline.Contains(process.Id))
                .Select(process => process.Id)
                .ToArray();
            if (survivors.Length == 0)
                break;
            await Task.Delay(100);
        }
        while (DateTimeOffset.UtcNow < deadline);

        survivors.Should().BeEmpty();
    }

    [Fact]
    [Trait("Category", "WindowsSecurityIntegration")]
    public async Task Interrupted_Runs_Leave_No_Staged_Job_Directories_Or_Workers()
    {
        var jobsRoot = SandboxJobsRoot();
        var baselineJobs = SnapshotJobDirectories(jobsRoot);
        var baselineWorkers = Process.GetProcessesByName("cmd")
            .Select(process => process.Id).ToHashSet();
        var baselineFailures = WindowsAppContainerProcessRunner.FailedTemporaryCleanups;

        using (var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(400)))
        {
            try
            {
                await RunCmdAsync(
                    "for /L %i in (1,1,2147483647) do @rem",
                    _ => { },
                    cts.Token);
            }
            catch (OperationCanceledException)
            {
            }
        }

        try
        {
            await RunCmdAsync(
                "for /L %i in (1,1,300000) do @echo xxxxxxxxxx",
                _ => { },
                CancellationToken.None);
        }
        catch (SandboxOutputLimitException)
        {
        }

        await Task.Delay(1000);
        SnapshotJobDirectories(jobsRoot).Except(baselineJobs).Should().BeEmpty(
            "an interrupted sandbox run must not strand its staged workspace");
        WindowsAppContainerProcessRunner.FailedTemporaryCleanups
            .Should().Be(baselineFailures);
        Process.GetProcessesByName("cmd")
            .Where(process => !baselineWorkers.Contains(process.Id))
            .Should().BeEmpty();
    }

    private static string SandboxJobsRoot() =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Packages",
            "sran.desktop.commandsandbox.v1",
            "AC",
            "LocalState",
            "SranJobs");

    private static HashSet<string> SnapshotJobDirectories(string jobsRoot) =>
        Directory.Exists(jobsRoot)
            ? Directory.GetDirectories(jobsRoot).ToHashSet(StringComparer.OrdinalIgnoreCase)
            : [];

    private Task<int> RunCmdAsync(
        string command,
        Action<string> output,
        CancellationToken ct)
    {
        var system = Environment.GetFolderPath(Environment.SpecialFolder.System);
        return _runner.RunAsync(
            new ProcessRunSpec(
                Path.Combine(system, "cmd.exe"),
                $"/d /q /s /c \"{command}\"",
                _workspace),
            output,
            ct);
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
            Directory.Delete(_root, recursive: true);
    }
}
