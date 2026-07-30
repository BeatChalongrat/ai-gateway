using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// P3-3: the host lane must answer the same way about a directory whether or not
/// the caller named it.
///
/// <para>The defect: with no <c>working_directory</c> argument the host lane fell
/// back to <c>%USERPROFILE%</c> directly, while passing that same path explicitly
/// went through <see cref="PathGuard.Resolve"/> and was refused, because the
/// built-in roots had been removed and the profile is not a configured mount. One
/// directory, two answers, depending only on whether it was mentioned. That is a
/// seam left by a partial change, not a security policy — a host-lane command runs
/// with the user's full rights either way, so refusing to <em>start</em> it
/// somewhere restrains nothing.</para>
///
/// <para>The sandbox lane is asserted to keep the opposite rule, because there the
/// directory really is the boundary: it is the tree staged into the container.</para>
/// </summary>
[Collection("PathGuardGrants")]
public sealed class HostLaneWorkingDirectoryTests : IDisposable
{
    private readonly string _mountDir;

    public HostLaneWorkingDirectoryTests()
    {
        _mountDir = Path.Combine(Path.GetTempPath(), $"sran-hostlane-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_mountDir);
        // Deliberately NOT granting the user profile: the point is that the host
        // lane can start there without it being a mount.
        PathGuard.ConfigureMounts(
            [new WorkspaceMount(_mountDir, WorkspaceMountAccess.ReadWrite)],
            _mountDir);
    }

    private sealed class FakeSettings : ISettingsService
    {
        public UserSettings Current { get; }
        public bool IsFirstRun => false;
        public FakeSettings(string lane, string workingFolder) => Current = new UserSettings
        {
            ShellExecutionMode = lane,
            WorkingFolder = workingFolder,
            ShellTimeoutSeconds = 30
        };
        public event EventHandler? SettingsChanged;
        public void Save(UserSettings settings) => SettingsChanged?.Invoke(this, EventArgs.Empty);
    }

    /// <summary>Captures the working directory the tool decided on, without running
    /// anything. Returning exit 0 keeps the tool on its success path.</summary>
    private sealed class CapturingRunner : IProcessRunner
    {
        public string? WorkingDirectory { get; private set; }
        public Task<int> RunAsync(ProcessRunSpec spec, Action<string> onLine, CancellationToken ct)
        {
            WorkingDirectory = spec.WorkingDirectory;
            return Task.FromResult(0);
        }
    }

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    private static (RunCommandTool Tool, CapturingRunner Runner) HostLaneTool(
        string workingFolder = "")
    {
        var runner = new CapturingRunner();
        var tool = new RunCommandTool(
            NullLogger<RunCommandTool>.Instance,
            new FakeSettings("host", workingFolder),
            session: null,
            processRunner: runner);
        return (tool, runner);
    }

    [Fact]
    public async Task Host_Lane_Implicit_Fallback_And_Explicit_Same_Path_Agree()
    {
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

        var (implicitTool, implicitRunner) = HostLaneTool();
        var implicitResult = await implicitTool.ExecuteAsync(
            Args(new { command = "echo seam" }), CancellationToken.None);

        var (explicitTool, explicitRunner) = HostLaneTool();
        var explicitResult = await explicitTool.ExecuteAsync(
            Args(new { command = "echo seam", working_directory = profile }),
            CancellationToken.None);

        // Before the fix the implicit call succeeded and the explicit one failed
        // with "outside the allowed directories" — same directory, two verdicts.
        explicitResult.Success.Should().Be(
            implicitResult.Success,
            "naming the directory the lane would have chosen anyway must not change "
            + "the outcome");
        implicitResult.Success.Should().BeTrue();
        explicitRunner.WorkingDirectory.Should().Be(
            implicitRunner.WorkingDirectory,
            "both paths must resolve to the same canonical directory");
        implicitRunner.WorkingDirectory.Should().Be(profile);
    }

    [Fact]
    public async Task Host_Lane_Accepts_A_Directory_Outside_Every_Mount()
    {
        // A command that touches no files at all — `docker ps`, `git --version`,
        // `ipconfig` — used to be rejected purely because the user had not
        // nominated an unrelated folder first.
        var outside = Path.Combine(Path.GetTempPath(), $"sran-unmounted-{Guid.NewGuid():N}");
        Directory.CreateDirectory(outside);
        try
        {
            var (tool, runner) = HostLaneTool();

            var result = await tool.ExecuteAsync(
                Args(new { command = "echo outside", working_directory = outside }),
                CancellationToken.None);

            result.Success.Should().BeTrue();
            runner.WorkingDirectory.Should().Be(outside);
        }
        finally
        {
            Directory.Delete(outside, recursive: true);
        }
    }

    [Fact]
    public async Task Host_Lane_Still_Refuses_A_Directory_That_Does_Not_Exist()
    {
        var (tool, runner) = HostLaneTool();

        var result = await tool.ExecuteAsync(
            Args(new
            {
                command = "echo nope",
                working_directory = Path.Combine(_mountDir, "no-such-subdir")
            }),
            CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("not found");
        runner.WorkingDirectory.Should().BeNull("nothing should have been dispatched");
    }

    /// <summary>The app's own state stays unreachable in every lane. Relaxing the
    /// mount requirement must not relax this.</summary>
    [Fact]
    public async Task Host_Lane_Still_Refuses_The_Apps_Own_State_Directory()
    {
        var stateDir = SranDesktop.Application.Services.FolderGrantValidator.AppStateDir;
        Directory.CreateDirectory(stateDir);
        var (tool, runner) = HostLaneTool();

        var result = await tool.ExecuteAsync(
            Args(new { command = "dir", working_directory = stateDir }),
            CancellationToken.None);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("application data");
        runner.WorkingDirectory.Should().BeNull();
    }

    /// <summary>The sandbox lane keeps the mount requirement: it stages the tree and
    /// runs inside the copy, so an unmounted directory has nothing to execute in.</summary>
    [Fact]
    public async Task Sandbox_Lane_Still_Requires_An_Authorized_Directory()
    {
        var outside = Path.Combine(Path.GetTempPath(), $"sran-unmounted-{Guid.NewGuid():N}");
        Directory.CreateDirectory(outside);
        try
        {
            var runner = new CapturingRunner();
            var tool = new RunCommandTool(
                NullLogger<RunCommandTool>.Instance,
                new FakeSettings("sandbox", _mountDir),
                session: null,
                processRunner: runner);

            var result = await tool.ExecuteAsync(
                Args(new { command = "echo outside", working_directory = outside }),
                CancellationToken.None);

            result.Success.Should().BeFalse();
            runner.WorkingDirectory.Should().BeNull();
        }
        finally
        {
            Directory.Delete(outside, recursive: true);
        }
    }

    /// <summary>An unrecognized settings value must resolve to the sandbox, not the
    /// host. A truncated write or a value from a future version has to fail toward
    /// isolation.</summary>
    [Theory]
    [InlineData("")]
    [InlineData("hos")]
    [InlineData("HOST ")]
    [InlineData("anything-else")]
    public void Unknown_Lane_Values_Parse_To_Sandbox_Except_Host(string value)
    {
        var parsed = SranDesktop.Domain.Enums.ShellLaneParser.Parse(value);

        if (value.Trim().Equals("host", StringComparison.OrdinalIgnoreCase))
            parsed.Should().Be(SranDesktop.Domain.Enums.ShellLane.Host);
        else
            parsed.Should().Be(SranDesktop.Domain.Enums.ShellLane.Sandbox);
    }

    public void Dispose()
    {
        PathGuard.ConfigureMounts(Array.Empty<WorkspaceMount>());
        try { Directory.Delete(_mountDir, recursive: true); } catch (IOException) { }
    }
}
