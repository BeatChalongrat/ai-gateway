using System.Text;
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
        "Run a command in Windows PowerShell 5.1 (the default interpreter — use PowerShell syntax: Get-ChildItem, " +
        "Get-Date, Select-Object, pipelines) and return stdout+stderr and the exit code. For a cmd.exe built-in " +
        "prefix the command with `cmd /c` (e.g. `cmd /c dir /b`); a `powershell …` or `pwsh …` prefix runs that " +
        "interpreter with your own flags. Long-running commands (builds, tests, installs) are allowed; the timeout " +
        "is configurable and defaults high. Set working_directory once and it is remembered for the following " +
        "commands in this conversation, so a multi-step build stays in the project folder.";
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
        var (shell, shellArgs) = PickShell(command);
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

    // FIX31 Part 3. Before this round the default interpreter was cmd.exe, and a
    // `powershell …` prefix was re-wrapped in our own `-Command`, so the model's
    // `powershell -Command "Get-Date"` became `-NoProfile -NonInteractive -Command
    // -Command "Get-Date"` and failed on "-Command is not recognized" — while a
    // bare `Get-Date` failed in cmd. Measured side by side (FIX31 report): only
    // `powershell "…"` (no flag) worked, which is how one sweep call passed.
    //
    // Now: Windows PowerShell 5.1 is the default (every cmdlet the model reached
    // for in the sweeps is a PowerShell cmdlet; cmd is the legacy shell). The
    // command text travels as -EncodedCommand so quotes, pipes and non-ASCII
    // survive the Win32 command-line parser untouched; output is forced to
    // UTF-8 (the process runner reads UTF-8); the native exit code is
    // propagated. A named interpreter — `cmd …`, `powershell …`, `pwsh …` —
    // runs VERBATIM with the model's own flags (only -NoProfile/-NonInteractive
    // are added when absent, so a stray profile script or Read-Host cannot hang
    // the call). The prompt states exactly this (prompt-assembler.ts), and the
    // tests below pin both the statement and the behaviour.
    private static readonly System.Text.RegularExpressions.Regex NamedInterpreter =
        new(@"^\s*(?<name>pwsh|powershell|cmd)(?:\.exe)?(?:\s+(?<rest>.*))?$",
            System.Text.RegularExpressions.RegexOptions.IgnoreCase | System.Text.RegularExpressions.RegexOptions.Singleline);

    internal static (string Shell, string Args) PickShell(string command)
    {
        var m = NamedInterpreter.Match(command);
        if (m.Success)
        {
            var rest = (m.Groups["rest"].Success ? m.Groups["rest"].Value : "").Trim();
            switch (m.Groups["name"].Value.ToLowerInvariant())
            {
                case "cmd":
                    // "cmd /c dir /b" → cmd.exe /c dir /b ; "cmd dir /b" → cmd.exe /c dir /b
                    if (!rest.StartsWith("/c", StringComparison.OrdinalIgnoreCase)
                        && !rest.StartsWith("/k", StringComparison.OrdinalIgnoreCase))
                        rest = "/c " + rest;
                    return ("cmd.exe", rest);
                case "pwsh":
                    return ("pwsh.exe", WithQuietFlags(rest));
                default:
                    return ("powershell.exe", WithQuietFlags(rest));
            }
        }

        // With stdout/stderr redirected, powershell.exe serialises its error and
        // progress streams to stderr as CLIXML ("#< CLIXML <Objs …"), which is
        // noise to a model. So: progress silenced, and the command runs inside a
        // block whose error stream is merged (2>&1) into plain text on stdout.
        // `$?` is captured INSIDE the block, right after the last statement (the
        // `$?` seen after `& { }` reports the invocation, not the inner error);
        // a native exit code wins when there is one.
        var script =
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $OutputEncoding=[System.Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'\n" +
            "& {\n" +
            command + "\n" +
            "$global:__ok = $?\n" +
            "} 2>&1 | Out-String -Stream\n" +
            "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE } elseif (-not $global:__ok) { exit 1 }";
        var encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
        return ("powershell.exe", "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " + encoded);
    }

    private static string WithQuietFlags(string rest)
    {
        var flags = "";
        if (!rest.Contains("-noprofile", StringComparison.OrdinalIgnoreCase)) flags += "-NoProfile ";
        if (!rest.Contains("-noninteractive", StringComparison.OrdinalIgnoreCase)) flags += "-NonInteractive ";
        return (flags + rest).Trim();
    }
}
