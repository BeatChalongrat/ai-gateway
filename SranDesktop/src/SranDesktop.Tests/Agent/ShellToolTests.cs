using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Phase A PART 3: configurable timeout, partial output on timeout,
/// per-conversation working-directory retention.</summary>
public class ShellToolTests : IDisposable
{
    private readonly string _workDir;

    public ShellToolTests()
    {
        _workDir = Path.Combine(Path.GetTempPath(), $"sran-shell-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_workDir);
    }

    private sealed class FakeSettings : ISettingsService
    {
        public UserSettings Current { get; }
        public bool IsFirstRun => false;
        public FakeSettings(int timeout) => Current = new UserSettings { ShellTimeoutSeconds = timeout };
        public event EventHandler? SettingsChanged;
        public void Save(UserSettings settings) => SettingsChanged?.Invoke(this, EventArgs.Empty);
    }

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    [Fact]
    public async Task Command_Completes_Under_Configured_Timeout()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance, new FakeSettings(300));

        var result = await tool.ExecuteAsync(Args(new { command = "echo build-ok" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("build-ok");
        result.Content.Should().Contain("[exit code 0]");
    }

    [Fact]
    public async Task Timeout_Returns_Partial_Output_And_Status_Not_A_Bare_Kill()
    {
        // A 1s override on a 5s sleep: the old behavior was a bare "timed out and
        // was killed" failure; now it returns partial output + a resumable note.
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance, new FakeSettings(300));

        var result = await tool.ExecuteAsync(
            Args(new { command = "powershell Start-Sleep -Seconds 5", timeout_seconds = 1 }),
            CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("still running");
        result.Content.Should().Contain("timeout_seconds");
    }

    [Fact]
    public async Task Stderr_And_NonZero_Exit_Feed_Back()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance, new FakeSettings(300));

        var result = await tool.ExecuteAsync(
            Args(new { command = "echo oops 1>&2 & exit /b 3" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        result.Content.Should().Contain("oops");
        result.Content.Should().Contain("[exit code 3]");
    }

    [Fact]
    public async Task WorkingDirectory_Persists_Across_Calls_In_A_Conversation()
    {
        var session = new ShellSession { CurrentConversationId = "conv-1" };
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance, new FakeSettings(300), session);
        var subA = Path.Combine(_workDir, "projA");
        var subB = Path.Combine(_workDir, "projB");
        Directory.CreateDirectory(subA);
        Directory.CreateDirectory(subB);

        // Set the cwd explicitly once...
        var first = await tool.ExecuteAsync(
            Args(new { command = "cd", working_directory = subA }), CancellationToken.None);
        first.Content.Should().Contain(subA);

        // ...the next command without a working_directory reuses it.
        var second = await tool.ExecuteAsync(Args(new { command = "cd" }), CancellationToken.None);
        second.Content.Should().Contain(subA);

        // A new explicit dir updates the retained value.
        var third = await tool.ExecuteAsync(
            Args(new { command = "cd", working_directory = subB }), CancellationToken.None);
        third.Content.Should().Contain(subB);
        session.GetWorkingDirectory("conv-1").Should().Be(subB);
    }

    [Fact]
    public async Task WorkingDirectory_Is_Isolated_Per_Conversation()
    {
        var subA = Path.Combine(_workDir, "cA");
        Directory.CreateDirectory(subA);
        var session = new ShellSession();
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance, new FakeSettings(300), session);

        session.CurrentConversationId = "conv-A";
        await tool.ExecuteAsync(Args(new { command = "cd", working_directory = subA }), CancellationToken.None);

        // Another conversation has no retained dir → falls back to user profile.
        session.CurrentConversationId = "conv-B";
        var other = await tool.ExecuteAsync(Args(new { command = "cd" }), CancellationToken.None);
        other.Content.Should().NotContain(subA);
        session.GetWorkingDirectory("conv-B").Should().BeNull();
    }

    [Fact]
    public async Task Retained_Working_Directory_Still_Rejects_Out_Of_Root_Cwd()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance, new FakeSettings(300),
            new ShellSession { CurrentConversationId = "c" });

        var result = await tool.ExecuteAsync(
            Args(new { command = "echo hi", working_directory = "C:\\Windows" }), CancellationToken.None);

        result.Success.Should().BeFalse();
    }

    public void Dispose()
    {
        if (Directory.Exists(_workDir))
            Directory.Delete(_workDir, recursive: true);
    }
}
