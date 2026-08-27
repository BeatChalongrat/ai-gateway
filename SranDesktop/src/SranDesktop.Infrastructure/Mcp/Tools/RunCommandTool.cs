using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class RunCommandTool : IMcpTool, ISupportsOutputStreaming
{
    private const int DefaultTimeoutSeconds = 300;
    private const int MinTimeoutSeconds = 5;
    private const int MaxTimeoutSeconds = 3600;

    // Destructive-command net (DestructiveCommandDetector): tokenized
    // command-shape rules, replacing the old substring blocklist that both
    // over-matched (--format flags) and under-matched (Format-Volume).
    // TIER-AWARE by owner decision (2026-08-04): Manual + Auto approval
    // modes enforce the net; Skip-all approvals bypasses it entirely — the
    // user opted out of every backstop — but each destructive command that
    // runs under Skip is audit-logged. The consent dialog (which shows the
    // exact command) remains the primary control in Manual/Auto.

    private readonly ILogger<RunCommandTool> _logger;
    private readonly ISettingsService? _settings;
    private readonly ShellSession? _session;
    private readonly IProcessRunner _processRunner;

    /// <summary>Live stdout/stderr as the command runs (Phase B PART 1).</summary>
    public event Action<ToolOutputChunk>? OutputStreamed;

    public RunCommandTool(
        ILogger<RunCommandTool> logger,
        ISettingsService? settings = null,
        ShellSession? session = null,
        IProcessRunner? processRunner = null)
    {
        _logger = logger;
        _settings = settings;
        _session = session;
        _processRunner = processRunner ?? new SystemProcessRunner();
    }

    public string Name => "run_command";
    public string Description =>
        "Execute a Windows shell command (cmd.exe / PowerShell) and return stdout+stderr and the exit code. " +
        "Long-running commands (builds, tests, installs) are allowed; the timeout is configurable and defaults " +
        "high. Set working_directory once and it is remembered for the following commands in this conversation, " +
        "so a multi-step build stays in the project folder.";
    public ToolCategory Category => ToolCategory.Shell;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "command": { "type": "string", "description": "The shell command to run" },
                "working_directory": { "type": "string", "description": "Optional working directory. Once set, it is retained for subsequent commands in this conversation (defaults to the last used, or the user profile)." },
                "timeout_seconds": { "type": "integer", "description": "Optional override for this command's timeout (5-3600s). Defaults to the configured shell timeout." }
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
        var threat = DestructiveCommandDetector.Analyze(command);
        if (threat is not null)
        {
            // No settings wired (tests / nested runs) → parser default Auto
            // → the net stays on. Fail-safe.
            var mode = ApprovalModeParser.Parse(_settings?.Current.ApprovalMode);
            if (mode != ApprovalMode.Skip)
                return ToolResult.Fail(
                    $"Command rejected by safety blocklist (matched \"{threat.Keyword}\", rule {threat.Rule}).");

            // Skip-all approvals: the user explicitly opted out of every
            // backstop — run it, but leave an audit trail. Log the rule and
            // the full command: under Skip the command executes anyway, so
            // the log adds no exposure it doesn't already have.
            _logger.LogWarning(
                "run_command: destructive command executed under Skip-all approvals " +
                "(rule={Rule}, matched={Keyword}): {Command}",
                threat.Rule, threat.Keyword, command);
        }

        var workingDirectory = ResolveWorkingDirectory(arguments, out var wdError);
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

        _logger.LogInformation("run_command: {Command} (cwd={Cwd}, timeout={Timeout}s)",
            command, workingDirectory, timeout.TotalSeconds);

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(timeout);
        int exitCode;
        try
        {
            exitCode = await _processRunner.RunAsync(spec, OnLine, timeoutCts.Token);
        }
        catch (OperationCanceledException)
        {
            if (ct.IsCancellationRequested)
                throw;

            // Timed out (not user-cancelled): return the partial output + a
            // resumable note rather than a bare kill (Phase A behavior kept).
            string partial;
            lock (gate) partial = output.ToString().TrimEnd();
            var note = $"[Command still running after {timeout.TotalSeconds:0}s and was stopped. " +
                       "The output above is partial. Increase timeout_seconds for a longer build, " +
                       "or split the work into smaller commands.]";
            return ToolResult.Ok(partial.Length == 0 ? note : $"{partial}\n{note}");
        }

        // The model's final result is still the full captured output + exit code
        // (streaming is additive UI); the ToolResult 50K cap applies downstream.
        string text;
        lock (gate) text = output.ToString().TrimEnd();
        var summary = $"[exit code {exitCode}]";
        return ToolResult.Ok(text.Length == 0 ? summary : $"{text}\n{summary}");
    }

    /// <summary>Resolves the working directory: an explicit one (validated, then
    /// remembered for this conversation) wins; otherwise the conversation's last
    /// used directory; otherwise the user profile.</summary>
    private string ResolveWorkingDirectory(JsonElement arguments, out string? error)
    {
        error = null;
        var conversationId = _session?.CurrentConversationId;
        // FIX26 Part 3: the fallback is the FIRST granted folder, never the user
        // profile. With nothing granted a command has nowhere to run: refuse.
        var fallback = PathGuard.DefaultRoot() ?? Path.GetTempPath();

        if (ToolHelpers.GetString(arguments, "working_directory") is { Length: > 0 } wd)
        {
            var resolved = PathGuard.Resolve(wd, out var resolveError);
            if (resolved is null) { error = resolveError; return fallback; }
            if (!Directory.Exists(resolved)) { error = $"Working directory not found: {resolved}"; return fallback; }
            if (conversationId is not null)
                _session!.SetWorkingDirectory(conversationId, resolved);
            return resolved;
        }

        if (conversationId is not null
            && _session!.GetWorkingDirectory(conversationId) is { } saved
            && Directory.Exists(saved)
            && PathGuard.Resolve(saved, out _) is not null)
            return saved;

        // No grant at all → the temp staging root (inside the fence, never the
        // profile). The system prompt already tells the model nothing is granted.
        return fallback;
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

    private static (string Shell, string Args) PickShell(string normalized, string command)
    {
        if (normalized.StartsWith("pwsh "))
            return ("pwsh.exe", "-NoProfile -NonInteractive -Command " + command[5..]);
        if (normalized.StartsWith("powershell "))
            return ("powershell.exe", "-NoProfile -NonInteractive -Command " + command[11..]);
        return ("cmd.exe", "/c " + command);
    }
}
