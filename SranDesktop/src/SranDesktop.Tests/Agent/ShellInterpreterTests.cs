using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>FIX31 Part 3: which interpreter run_command actually uses. Pins the
/// statement the prompt makes ("Windows PowerShell 5.1 by default; `cmd /c` for a
/// cmd built-in; a named interpreter verbatim") against the real behaviour.
/// Before this round the default was cmd.exe (a bare Get-Date: "not recognized")
/// and `powershell -Command "x"` was re-wrapped in a second -Command and failed.</summary>
public class ShellInterpreterTests
{
    private const string DefaultPrefix = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ";

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    [Fact]
    public void Bare_Command_Runs_In_PowerShell_As_EncodedCommand()
    {
        var (shell, args) = RunCommandTool.PickShell("Get-Date -Format o");
        shell.Should().Be("powershell.exe");
        args.Should().StartWith(DefaultPrefix);
        var script = System.Text.Encoding.Unicode.GetString(Convert.FromBase64String(args[DefaultPrefix.Length..]));
        script.Should().Contain("\nGet-Date -Format o\n");
        script.Should().Contain("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8");
        script.Should().Contain("exit $LASTEXITCODE");
    }

    [Theory]
    [InlineData("cmd /c dir /b", "cmd.exe", "/c dir /b")]
    [InlineData("cmd.exe /c echo hi", "cmd.exe", "/c echo hi")]
    [InlineData("cmd dir /b", "cmd.exe", "/c dir /b")]
    public void Cmd_Prefix_Runs_Cmd_Verbatim(string command, string shell, string args)
    {
        RunCommandTool.PickShell(command).Should().Be((shell, args));
    }

    [Theory]
    // The model's own flags are kept — no second -Command is wrapped around them.
    [InlineData("powershell -Command \"Get-Date -Format o\"", "powershell.exe", "-NoProfile -NonInteractive -Command \"Get-Date -Format o\"")]
    [InlineData("powershell -NoProfile -Command \"Get-Date\"", "powershell.exe", "-NonInteractive -NoProfile -Command \"Get-Date\"")]
    [InlineData("powershell \"Get-Date\"", "powershell.exe", "-NoProfile -NonInteractive \"Get-Date\"")]
    [InlineData("pwsh -c Get-Date", "pwsh.exe", "-NoProfile -NonInteractive -c Get-Date")]
    public void Named_PowerShell_Runs_With_The_Models_Own_Flags(string command, string shell, string args)
    {
        RunCommandTool.PickShell(command).Should().Be((shell, args));
    }

    [Fact]
    public async Task Bare_Cmdlet_Actually_Works_End_To_End()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance);
        var result = await tool.ExecuteAsync(Args(new { command = "Get-Date -Format o" }), CancellationToken.None);
        result.Success.Should().BeTrue();
        result.Content.Should().MatchRegex(@"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"); // an ISO timestamp, not "not recognized"
        result.Content.Should().Contain("[exit code 0]");
        result.Content.Should().NotContain("CLIXML"); // no serialised stream noise
    }

    [Fact]
    public async Task A_Failing_Cmdlet_Prints_Its_Error_As_Text_Not_CLIXML()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance);
        var result = await tool.ExecuteAsync(Args(new { command = "Get-Item C:\\definitely\\not\\here.txt" }), CancellationToken.None);
        result.Content.Should().Contain("Cannot find path").And.NotContain("CLIXML").And.NotContain("[exit code 0]");
    }

    [Fact]
    public async Task Model_Style_PowerShell_Prefix_With_Command_Flag_Works()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance);
        var result = await tool.ExecuteAsync(Args(new { command = "powershell -Command \"Get-Date -Format o\"" }), CancellationToken.None);
        result.Content.Should().MatchRegex(@"\d{4}-\d{2}-\d{2}T").And.Contain("[exit code 0]");
    }

    [Fact]
    public async Task Cmd_Builtin_Works_Through_The_Prefix()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance);
        var ok = await tool.ExecuteAsync(Args(new { command = "cmd /c echo from-cmd" }), CancellationToken.None);
        ok.Content.Should().Contain("from-cmd").And.Contain("[exit code 0]");
        var fail = await tool.ExecuteAsync(Args(new { command = "cmd /c exit 3" }), CancellationToken.None);
        fail.Content.Should().Contain("[exit code 3]");
    }

    [Fact]
    public async Task Native_Exit_Code_Propagates_From_The_Default_Path()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance);
        // `& cmd …` is a PowerShell call, not the `cmd` prefix rule.
        var native = await tool.ExecuteAsync(Args(new { command = "& cmd /c exit 4; Write-Output done" }), CancellationToken.None);
        native.Content.Should().Contain("done").And.Contain("[exit code 4]");
    }

    [Fact]
    public async Task NonAscii_Output_Survives()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance);
        var result = await tool.ExecuteAsync(Args(new { command = "Write-Output \"ไทย ok\"" }), CancellationToken.None);
        result.Content.Should().Contain("ไทย ok").And.Contain("[exit code 0]");
    }

    [Fact]
    public async Task A_Failing_Cmdlet_Is_A_NonZero_Exit()
    {
        var tool = new RunCommandTool(NullLogger<RunCommandTool>.Instance);
        var result = await tool.ExecuteAsync(Args(new { command = "Get-Item C:\\definitely\\not\\here.txt" }), CancellationToken.None);
        result.Content.Should().NotContain("[exit code 0]");
    }
}
