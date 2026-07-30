using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class RunCommandTool : IMcpTool, ISupportsOutputStreaming, ISensitiveDataTool
{
    private const int DefaultTimeoutSeconds = 300;
    private const int MinTimeoutSeconds = 5;
    private const int MaxTimeoutSeconds = 3600;

    // Defense-in-depth only. AppContainer isolation and workspace mounts are
    // the security boundary; command classification and consent cannot widen it.
    private static readonly string[] Blocklist =
    {
        "format ", "del /s", "del /f /s", "rmdir /s", "rd /s", "rm -rf", "rm -fr",
        "shutdown", "restart-computer", "mkfs", "dd if=", ":(){", "reg delete hklm",
        "cipher /w", "diskpart", "bcdedit"
    };

    /// <summary>What cmd.exe and PowerShell say when the executable is not on
    /// PATH. In the sandbox lane PATH is only System32, so this is the expected
    /// answer for every host tool and deserves a real explanation rather than a
    /// bare non-zero exit code.</summary>
    private static readonly string[] ExecutableNotFoundMarkers =
    {
        // cmd.exe
        "is not recognized as an internal or external command",
        // PowerShell; the article changed between versions, so both are listed.
        "is not recognized as a name of a cmdlet",
        "is not recognized as the name of a cmdlet",
        "commandnotfoundexception"
    };

    private readonly ILogger<RunCommandTool> _logger;
    private readonly ISettingsService? _settings;
    private readonly ShellSession? _session;
    private readonly ISecurityEventLogger? _securityEvents;

    /// <summary>Set only by tests that inject a runner. When present it pins the
    /// lane, so a test never silently picks up the user's real setting.</summary>
    private readonly IProcessRunner? _pinnedRunner;

    private readonly IProcessRunner _sandboxRunner = new WindowsAppContainerProcessRunner();
    private readonly IProcessRunner _hostRunner = new HostProcessRunner();

    /// <summary>Live stdout/stderr as the command runs (Phase B PART 1).</summary>
    public event Action<ToolOutputChunk>? OutputStreamed;

    public RunCommandTool(
        ILogger<RunCommandTool> logger,
        ISettingsService? settings = null,
        ShellSession? session = null,
        IProcessRunner? processRunner = null,
        ISecurityEventLogger? securityEvents = null)
    {
        _logger = logger;
        _settings = settings;
        _session = session;
        _pinnedRunner = processRunner;
        _securityEvents = securityEvents;
    }

    /// <summary>
    /// Picks the execution lane from settings — never from a failure.
    ///
    /// <para>A sandbox error returns an error. It must not re-run the command
    /// on the host, because that converts an isolation defect into a silent
    /// privilege escalation. The user opts into the host lane ahead of time or
    /// not at all.</para>
    ///
    /// <para>The string is interpreted by <see cref="ShellLaneParser"/>, which
    /// the settings UI also uses. One parser means the pill the user sees and
    /// the runner that executes can never disagree about which lane is active,
    /// and an unrecognized value resolves to the sandbox.</para>
    /// </summary>
    private bool HostLaneRequested =>
        ShellLaneParser.Parse(_settings?.Current.ShellExecutionMode) == ShellLane.Host;

    public string Name => "run_command";
    public string Description =>
        "Execute a Windows shell command (cmd.exe / PowerShell) and return stdout+stderr and the exit code. " +
        "Long-running commands (builds, tests, installs) are allowed; the timeout is configurable and defaults " +
        "high. Set working_directory once and it is remembered for the following commands in this conversation, " +
        "so a multi-step build stays in the project folder. " +
        "Commands run in one of two lanes, chosen by the user's settings and not selectable per call. " +
        "In the default sandbox lane there is no network and PATH is only System32, so host tooling such as " +
        "docker, git, node and python is unavailable; report that limitation instead of retrying variations " +
        "of the same command or hunting for the executable.";
    public ToolCategory Category => ToolCategory.Shell;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "Sandbox.Execute",
        "SandboxOutput.SendToModel",
        "shell_output",
        "unknown",
        "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false,
        MaxPayloadBytes: 2 * 1024 * 1024);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "command": { "type": "string", "description": "The shell command to run" },
                "working_directory": { "type": "string", "description": "Authorized workspace directory. Once set, it is retained for subsequent commands in this conversation." },
                "timeout_seconds": { "type": "integer", "description": "Optional override for this command's timeout (5-3600s). Defaults to the configured shell timeout." },
                "expected_exit_codes": {
                    "type": "array",
                    "description": "Exit codes that mean success for this step. Defaults to [0].",
                    "items": { "type": "integer" }
                }
            },
            "required": ["command"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var command = ToolHelpers.GetString(arguments, "command");
        if (string.IsNullOrWhiteSpace(command))
            return ToolResult.Fail("'command' is required.");

        var normalized = command.ToLowerInvariant().Trim();
        foreach (var blocked in Blocklist)
        {
            if (normalized.Contains(blocked))
                return ToolResult.Fail(
                    $"Command rejected by safety blocklist (matched \"{blocked.Trim()}\").");
        }

        // The lane is decided first because it changes what a working directory
        // means. In the sandbox lane the directory IS the security boundary — it
        // is the tree staged into the container — so there is nothing to run
        // without one. In the host lane it is only a starting directory; the
        // command can already reach the whole machine, so demanding an
        // authorized one blocks harmless commands without constraining harmful
        // ones. `docker ps`, `git --version` and `ipconfig` touch no files at
        // all, and gating them on a filesystem setting is a category error.
        // The lane comes from settings; the pinned runner only decides WHICH
        // object executes. Previously an injected runner also forced the lane to
        // sandbox, which made the host lane's own behaviour — its working-directory
        // rules and its audit record — impossible to test at all. With no settings
        // injected, HostLaneRequested is false, so a test still cannot drift onto
        // the host lane by accident.
        var onHostLane = HostLaneRequested;
        var runner = _pinnedRunner ?? (onHostLane ? _hostRunner : _sandboxRunner);

        var workingDirectory = ResolveWorkingDirectory(arguments, onHostLane, out var wdError);
        if (wdError is not null)
            return ToolResult.Fail(wdError);

        var timeout = ResolveTimeout(arguments);
        var (shell, shellArgs) = PickShell(normalized, command);
        var spec = new ProcessRunSpec(shell, shellArgs, workingDirectory);

        // Bounded so a flooding build can't blow memory/context (head+tail kept).
        var output = new BoundedOutputBuffer();
        var gate = new object();
        void OnLine(string line)
        {
            lock (gate) output.Append(line);
            // Live UI: append the line to the run_command card as it arrives.
            OutputStreamed?.Invoke(new ToolOutputChunk(Name, line));
        }

        var mount = PathGuard.ContainingRoot(workingDirectory);
        var relativeCwd = mount is null
            ? "[outside-workspace]"
            : Path.GetRelativePath(mount, workingDirectory);

        _logger.LogInformation(
            "run_command dispatch lane={Lane} shell={Shell} argumentCount={ArgumentCount} " +
            "commandFingerprint={CommandFingerprint} cwd={WorkspaceRelativeCwd} timeout={Timeout}s",
            onHostLane ? "host" : "sandbox",
            Path.GetFileName(shell),
            CountArguments(shellArgs),
            SensitiveDataRedactor.Fingerprint(command),
            relativeCwd,
            timeout.TotalSeconds);

        // Host-lane commands leave the sandbox, so the audit trail is the only
        // durable record of what ran on the real machine. It is written before
        // execution: a command that hangs or takes the process down still has
        // to appear in the log.
        if (onHostLane)
        {
            _securityEvents?.Write(new SecurityAuditEvent(
                "shell.host_lane_dispatch",
                $"user_opt_in:approval_mode={_settings?.Current.ApprovalMode ?? "auto"}",
                Name,
                Action: SensitiveDataRedactor.Fingerprint(command),
                RiskClass: "host_execution",
                TargetCategory: relativeCwd,
                ExternalTransmission: false));
        }

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(timeout);
        int exitCode;
        try
        {
            exitCode = await runner.RunAsync(spec, OnLine, timeoutCts.Token);
        }
        catch (OperationCanceledException)
        {
            if (ct.IsCancellationRequested)
                throw;

            string partial;
            lock (gate) partial = output.ToString().TrimEnd();
            var note = $"[Command still running after {timeout.TotalSeconds:0}s and was stopped. " +
                       "The output above is partial. Increase timeout_seconds for a longer build, " +
                       "or split the work into smaller commands.]";
            return ToolResult.FailedEffect(
                "Timeout",
                note,
                retryable: true,
                content: UntrustedObservationBoundary.ProtectIfUntrusted(Name, partial));
        }
        catch (SandboxWorkspaceSyncException ex)
        {
            return ToolResult.FailedEffect(
                "SandboxWorkspaceSyncFailed",
                ex.Message,
                retryable: false);
        }
        catch (SandboxExecutionException ex)
        {
            // Deliberately not retried on the host lane. A sandbox that cannot
            // start is a defect to surface, not a reason to widen privilege.
            return ToolResult.Fail(onHostLane
                ? $"Host execution blocked: {ex.Message}"
                : $"Sandbox execution blocked: {ex.Message}");
        }

        // The model's final result is still the full captured output + exit code
        // (streaming is additive UI); the ToolResult 50K cap applies downstream.
        string text;
        lock (gate) text = output.ToString().TrimEnd();
        // stdout/stderr is untrusted external content — wrap it so it can't act as
        // instructions. The exit-code summary is our own metadata and stays raw.
        var wrapped = UntrustedObservationBoundary.ProtectIfUntrusted(Name, text);
        var summary = $"[exit code {exitCode}]";
        var content = wrapped.Length == 0 ? summary : $"{wrapped}\n{summary}";
        var expectedExitCodes = ReadExpectedExitCodes(arguments);
        if (!expectedExitCodes.Contains(exitCode))
        {
            // "'docker' is not recognized" in the sandbox lane is not a broken
            // command — it is the lane working as designed, and reporting it as
            // a bare non-zero exit sends the caller hunting for the wrong bug.
            if (!onHostLane && LooksLikeExecutableNotFound(text))
                return ToolResult.FailedEffect(
                    "ExecutableNotAvailableInSandbox",
                    "The command was not found in the sandbox lane. Commands run inside a "
                    + "capability-free AppContainer whose PATH is only System32 and which "
                    + "has no network, so host tooling (docker, git, node, python) is not "
                    + "reachable there — a CLI that talks to a host service cannot work in "
                    + "a sandbox built to block host services. Built-in commands and files "
                    + "in the workspace do work. To run host tooling, the user must set "
                    + "shell_execution_mode to \"host\" in settings; the agent cannot switch "
                    + "lanes on its own.",
                    retryable: false,
                    exitCode: exitCode,
                    content: content);

            return ToolResult.FailedEffect(
                "NonZeroExitCode",
                $"Command exited with code {exitCode}; expected {string.Join(", ", expectedExitCodes)}.",
                retryable: true,
                exitCode: exitCode,
                content: content);
        }

        return ToolResult.Verified(
            content,
            new[] { $"process_exit_code={exitCode}" },
            exitCode: exitCode);
    }

    /// <summary>
    /// Resolves the working directory: an explicit one (validated, then
    /// remembered for this conversation) wins; otherwise the conversation's last
    /// used authorized directory; otherwise the configured working folder.
    ///
    /// <para>What happens when none of those exist depends on the lane, because
    /// the directory means different things in each. The sandbox lane stages
    /// that tree into the container and runs inside the copy, so without one
    /// there is literally nothing to execute in — it fails closed. The host lane
    /// runs on the real machine where the command can already reach everything;
    /// the directory is only where the shell starts. Requiring an authorized one
    /// there would reject <c>docker ps</c>, <c>git --version</c> and
    /// <c>ipconfig</c> — commands that touch no files — while doing nothing to
    /// restrain a command that does. So the host lane defaults instead of
    /// failing, and its real gates stay where they belong: the consent dialog
    /// and the blocklist.</para>
    /// </summary>
    private string ResolveWorkingDirectory(
        JsonElement arguments, bool onHostLane, out string? error)
    {
        error = null;
        var conversationId = _session?.CurrentConversationId;

        // One resolver per lane, applied to EVERY source of the directory —
        // explicit argument, remembered value, configured folder, and the
        // fallback. Mixing them is what produced the seam this replaces: the
        // fallback used to bypass the check that the explicit argument had to
        // pass, so `%USERPROFILE%` was accepted implicitly and refused by name.
        string? Resolve(string candidate, out string? resolveError) => onHostLane
            ? PathGuard.ResolveStartingDirectory(candidate, out resolveError)
            : AuthorizedWorkspaceDirectory(candidate, out resolveError);

        if (ToolHelpers.GetString(arguments, "working_directory") is { Length: > 0 } wd)
        {
            var resolved = Resolve(wd, out var resolveError);
            if (resolved is null) { error = resolveError; return string.Empty; }
            if (conversationId is not null)
                _session!.SetWorkingDirectory(conversationId, resolved);
            return resolved;
        }

        if (conversationId is not null
            && _session!.GetWorkingDirectory(conversationId) is { } saved
            && Resolve(saved, out _) is { } resolvedSaved)
            return resolvedSaved;

        if (_settings?.Current.WorkingFolder is { Length: > 0 } configured
            && Resolve(configured, out _) is { } resolvedConfigured)
            return resolvedConfigured;

        if (onHostLane)
        {
            var profile = Environment.GetFolderPath(
                Environment.SpecialFolder.UserProfile);
            // Through the same resolver as everything else, so the answer for the
            // profile does not depend on how the caller arrived at it.
            if (Resolve(profile, out var profileError) is { } resolvedProfile)
                return resolvedProfile;

            error = "Host shell execution could not resolve a starting directory: "
                  + (profileError ?? "the user profile is unavailable.");
            return string.Empty;
        }

        error = "Sandbox shell execution requires a working folder, because the sandbox "
              + "runs inside a staged copy of that folder. "
              + PathGuard.NoWorkspaceRemedy
              + " A command that needs no files at all (docker ps, git --version, "
              + "ipconfig) can instead run by switching \"Where commands run\" to "
              + "\"On this PC\" next to the message box.";
        return string.Empty;
    }

    /// <summary>Sandbox-lane directory resolution: the directory must be inside an
    /// authorized mount, because the sandbox stages that tree and runs in the
    /// copy. Existence is required too — there is nothing to stage otherwise.</summary>
    private static string? AuthorizedWorkspaceDirectory(string candidate, out string? error)
    {
        var resolved = PathGuard.Resolve(candidate, out error);
        if (resolved is null)
            return null;
        if (!Directory.Exists(resolved))
        {
            error = $"Working directory not found: {resolved}";
            return null;
        }
        return resolved;
    }

    private TimeSpan ResolveTimeout(JsonElement arguments)
    {
        var seconds = _settings?.Current.ShellTimeoutSeconds ?? DefaultTimeoutSeconds;
        if (arguments.ValueKind == JsonValueKind.Object
            && arguments.TryGetProperty("timeout_seconds", out var t)
            && t.ValueKind == JsonValueKind.Number
            && t.TryGetInt32(out var overrideSeconds))
            seconds = overrideSeconds;

        return TimeSpan.FromSeconds(Math.Clamp(seconds, MinTimeoutSeconds, MaxTimeoutSeconds));
    }

    private static HashSet<int> ReadExpectedExitCodes(JsonElement arguments)
    {
        var values = new HashSet<int>();
        if (arguments.ValueKind == JsonValueKind.Object
            && arguments.TryGetProperty("expected_exit_codes", out var codes)
            && codes.ValueKind == JsonValueKind.Array)
        {
            foreach (var code in codes.EnumerateArray())
                if (code.ValueKind == JsonValueKind.Number && code.TryGetInt32(out var value))
                    values.Add(value);
        }
        if (values.Count == 0) values.Add(0);
        return values;
    }

    /// <summary>Recognises the shell's "no such executable" text. Matched on the
    /// captured output rather than the exit code because cmd.exe and PowerShell
    /// both use exit 1 for this and for ordinary command failure.</summary>
    private static bool LooksLikeExecutableNotFound(string output)
    {
        if (string.IsNullOrWhiteSpace(output))
            return false;
        var lowered = output.ToLowerInvariant();
        return ExecutableNotFoundMarkers.Any(marker => lowered.Contains(marker));
    }

    private static int CountArguments(string arguments)
    {
        if (string.IsNullOrWhiteSpace(arguments))
            return 0;
        var count = 0;
        var inQuotes = false;
        var inToken = false;
        foreach (var character in arguments)
        {
            if (character == '"')
                inQuotes = !inQuotes;
            if (!inQuotes && char.IsWhiteSpace(character))
            {
                inToken = false;
                continue;
            }
            if (!inToken)
            {
                count++;
                inToken = true;
            }
        }
        return count;
    }

    private static (string Shell, string Args) PickShell(string normalized, string command)
    {
        var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        var system = Environment.GetFolderPath(Environment.SpecialFolder.System);
        var powerShell = Path.Combine(
            windows,
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe");
        if (normalized.StartsWith("pwsh "))
            return (powerShell,
                "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Restricted -Command " +
                command[5..]);
        if (normalized.StartsWith("powershell "))
            return (powerShell,
                "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Restricted -Command " +
                command[11..]);
        return (Path.Combine(system, "cmd.exe"), $"/d /q /s /c \"{command}\"");
    }
}
