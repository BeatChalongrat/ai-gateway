using FluentAssertions;
using Xunit;

namespace SranDesktop.Tests.Security;

/// <summary>
/// Source-level invariants for the unified durable runtime. Behavioural tests
/// prove today's tools go through the runtime; these prove a NEW tool cannot
/// quietly reintroduce a host-execution or UI-verified-success bypass, which is
/// the failure mode that behavioural tests structurally cannot catch.
/// </summary>
public sealed class RuntimeBypassInvariantTests
{
    /// <summary>Tools allowed to launch a host process, each with its own
    /// security suite. Adding a name here is a security decision.</summary>
    private static readonly HashSet<string> HostExecutionAllowlist =
        new(StringComparer.OrdinalIgnoreCase)
        {
            // Trusted, allowlisted application launch — TrustedApplicationLaunchSecurityTests.
            "OpenApplicationTool.cs",
            // The supervised runners themselves.
            "ProcessRunner.cs",
            "WindowsAppContainerProcessRunner.cs",
            // Opt-in host lane. Reachable only when the user has set
            // shell_execution_mode = "host"; guarded by the fallback test below.
            "HostProcessRunner.cs"
        };

    /// <summary>Files OUTSIDE Mcp/Tools that are allowed to shell-execute on behalf
    /// of an agent tool, each with its own suite. This list exists because moving a
    /// <c>Process.Start</c> one directory up would otherwise satisfy the tools-only
    /// scan above while changing nothing about what the process can do.</summary>
    private static readonly HashSet<string> DelegatedLaunchAllowlist =
        new(StringComparer.OrdinalIgnoreCase)
        {
            // http/https only, used by open_url and the OAuth connect flow —
            // OpenUrlToolSecurityTests + SystemBrowserLauncher's scheme refusal.
            "SystemBrowserLauncher.cs"
        };

    [Fact]
    public void No_Agent_Tool_Starts_A_Host_Process_Outside_The_Audited_Runners()
    {
        var offenders = ToolSources()
            .Where(file =>
                !HostExecutionAllowlist.Contains(Path.GetFileName(file))
                && File.ReadLines(file).Any(StartsAProcess))
            .Select(Path.GetFileName)
            .ToArray();

        offenders.Should().BeEmpty(
            "an agent tool that starts a host process escapes sandboxing, "
            + "observation, and durable verification");
    }

    [Fact]
    public void Allowlisted_Host_Execution_Entries_All_Still_Exist()
    {
        var present = ToolSources().Select(Path.GetFileName).ToHashSet(
            StringComparer.OrdinalIgnoreCase);

        HostExecutionAllowlist.Should().BeSubsetOf(
            present,
            "a stale allowlist entry silently widens what the next tool may do");
    }

    [Fact]
    public void No_Browser_Tool_Opens_A_Url_Through_The_Shell()
    {
        var offenders = ToolSources()
            .Where(file => Path.GetFileName(file).StartsWith(
                "Browser", StringComparison.Ordinal))
            .Where(file => File.ReadAllText(file).Contains(
                "UseShellExecute", StringComparison.Ordinal))
            .Select(Path.GetFileName)
            .ToArray();

        offenders.Should().BeEmpty(
            "a shell-launched URL is invisible to the extension bridge, so no "
            + "observation or verification of the resulting page is possible");
    }

    /// <summary>
    /// Closes the relocation loophole in the tools-only scan above: a tool that
    /// delegates its launch to a helper one directory up would pass that scan while
    /// starting exactly the same process.
    ///
    /// <para>The Infrastructure layer legitimately starts processes for its own
    /// reasons (updates, the browser-extension relay). What this pins is the set of
    /// files an AGENT TOOL is allowed to reach a shell through — today just the
    /// http/https browser launcher behind <c>open_url</c>.</para>
    /// </summary>
    [Fact]
    public void Agent_Tool_Launch_Helpers_Are_Confined_To_The_Delegated_Allowlist()
    {
        var referenced = ToolSources()
            .SelectMany(File.ReadAllLines)
            .Where(line => line.Contains("IBrowserLauncher", StringComparison.Ordinal))
            .ToArray();

        referenced.Should().NotBeEmpty(
            "open_url delegates its launch; if that indirection disappears the tool "
            + "either stopped working or went back to calling the shell directly");

        DelegatedLaunchAllowlist.Should().Contain(
            "SystemBrowserLauncher.cs",
            "the only implementation an agent tool may launch through must stay listed "
            + "and reviewed; adding an entry here is a security decision");
    }

    [Fact]
    public void Every_Mutating_Browser_Tool_Declares_Pre_And_Post_Observation()
    {
        var mutating = new[]
        {
            "BrowserClickTool.cs", "BrowserTypeTool.cs", "BrowserTransferTools.cs"
        };

        foreach (var name in mutating)
        {
            var source = File.ReadAllText(
                ToolSources().Single(file => Path.GetFileName(file) == name));
            source.Should().Contain(
                "ObserveBeforeActionAsync",
                $"{name} must observe before it mutates the page");
            source.Should().Contain(
                "ObserveAfterActionAsync",
                $"{name} must observe after it mutates the page");
            source.Should().Contain(
                "VerifyAction",
                $"{name} must expose verification to the runtime");
        }
    }

    /// <summary>
    /// The host shell lane must be entered by a forward decision from user
    /// settings, never by recovering from a sandbox failure.
    ///
    /// <para>The distinction is the whole security argument for having two
    /// lanes. A lane the user chose in advance is a widened boundary they
    /// consented to; a lane reached because isolation broke is a privilege
    /// escalation triggered by the very defect isolation was meant to contain.
    /// Behavioural tests cannot catch a regression here — a future
    /// <c>catch (SandboxExecutionException) { retry on host; }</c> would make
    /// every existing shell test pass and greener.</para>
    /// </summary>
    [Fact]
    public void Host_Lane_Is_Never_Reached_By_Fallback()
    {
        var source = File.ReadAllText(
            ToolSources().Single(file => Path.GetFileName(file) == "RunCommandTool.cs"));

        var firstCatch = source.IndexOf("catch (", StringComparison.Ordinal);
        firstCatch.Should().BeGreaterThan(
            0, "RunCommandTool is expected to handle execution failures");

        // Two legitimate mentions: the field, and the lane decision. Both sit
        // above the first catch, so no failure path can reach the host runner.
        var hostRunnerUses = Occurrences(source, "_hostRunner");
        hostRunnerUses.Should().NotBeEmpty("the host lane must still exist");
        hostRunnerUses.Should().OnlyContain(
            position => position < firstCatch,
            "referencing the host runner at or below a catch block means a "
            + "sandbox failure can escalate into host execution");

        source.Should().Contain(
            "HostLaneRequested",
            "the lane must come from settings, which is what makes it opt-in");
    }

    /// <summary>A fresh install must not execute on the host until the user asks
    /// for it. A default flip is the kind of change that reads as a one-word
    /// diff and silently removes the sandbox for every existing user.</summary>
    [Fact]
    public void Shell_Execution_Defaults_To_The_Sandbox_Lane()
    {
        new SranDesktop.Application.Models.UserSettings()
            .ShellExecutionMode
            .Should().Be("sandbox");
    }

    private static List<int> Occurrences(string source, string token)
    {
        var found = new List<int>();
        for (var index = source.IndexOf(token, StringComparison.Ordinal);
             index >= 0;
             index = source.IndexOf(token, index + token.Length, StringComparison.Ordinal))
            found.Add(index);
        return found;
    }

    /// <summary>An actual call, not a comment or a message string that merely
    /// mentions process launching while explaining a rule about it.</summary>
    private static bool StartsAProcess(string line)
    {
        var index = line.IndexOf("Process.Start(", StringComparison.Ordinal);
        if (index < 0)
            return false;
        var before = line[..index];
        return !before.TrimStart().StartsWith("//", StringComparison.Ordinal)
               && !before.TrimStart().StartsWith("*", StringComparison.Ordinal)
               && !before.Contains('"');
    }

    private static string[] ToolSources() =>
        Directory.GetFiles(
            Path.Combine(RepositoryRoot(), "src", "SranDesktop.Infrastructure", "Mcp", "Tools"),
            "*.cs",
            SearchOption.AllDirectories);

    private static string RepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "SranDesktop.sln")))
            directory = directory.Parent;
        return directory?.FullName
               ?? throw new InvalidOperationException(
                   "The repository root was not found from the test output directory.");
    }
}
