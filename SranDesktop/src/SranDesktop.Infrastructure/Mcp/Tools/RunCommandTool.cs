using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class RunCommandTool : IMcpTool
{
    private const int DefaultTimeoutSeconds = 300;
    private const int MinTimeoutSeconds = 5;
    private const int MaxTimeoutSeconds = 3600;

    // Defense-in-depth only — the consent dialog (which shows the exact
    // command) is the primary control.
    private static readonly string[] Blocklist =
    {
        "format ", "del /s", "del /f /s", "rmdir /s", "rd /s", "rm -rf", "rm -fr",
        "shutdown", "restart-computer", "mkfs", "dd if=", ":(){", "reg delete hklm",
        "cipher /w", "diskpart", "bcdedit"
    };

    private readonly ILogger<RunCommandTool> _logger;
    private readonly ISettingsService? _settings;
    private readonly ShellSession? _session;

    public RunCommandTool(
        ILogger<RunCommandTool> logger,
        ISettingsService? settings = null,
        ShellSession? session = null)
    {
        _logger = logger;
        _settings = settings;
        _session = session;
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
        foreach (var blocked in Blocklist)
        {
            if (normalized.Contains(blocked))
                return ToolResult.Fail(
                    $"Command rejected by safety blocklist (matched \"{blocked.Trim()}\").");
        }

        var workingDirectory = ResolveWorkingDirectory(arguments, out var wdError);
        if (wdError is not null)
            return ToolResult.Fail(wdError);

        var timeout = ResolveTimeout(arguments);
        var (shell, shellArgs) = PickShell(normalized, command);

        using var process = new Process();
        process.StartInfo = new ProcessStartInfo
        {
            FileName = shell,
            Arguments = shellArgs,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = workingDirectory,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };

        var output = new StringBuilder();
        process.OutputDataReceived += (_, e) => { if (e.Data is not null) lock (output) output.AppendLine(e.Data); };
        process.ErrorDataReceived += (_, e) => { if (e.Data is not null) lock (output) output.AppendLine(e.Data); };

        _logger.LogInformation("run_command: {Command} (cwd={Cwd}, timeout={Timeout}s)",
            command, workingDirectory, timeout.TotalSeconds);
        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(timeout);
        try
        {
            await process.WaitForExitAsync(timeoutCts.Token);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(entireProcessTree: true); } catch (Exception) { /* already gone */ }
            if (ct.IsCancellationRequested)
                throw;

            // Return the partial output + a clear "timed out" status rather than
            // a bare kill, so the model can decide what to do next (PART 3).
            string partial;
            lock (output) partial = output.ToString().TrimEnd();
            var note = $"[Command still running after {timeout.TotalSeconds:0}s and was stopped. " +
                       "The output above is partial. Increase timeout_seconds for a longer build, " +
                       "or split the work into smaller commands.]";
            return ToolResult.Ok(partial.Length == 0 ? note : $"{partial}\n{note}");
        }

        string text;
        lock (output) text = output.ToString().TrimEnd();
        var summary = $"[exit code {process.ExitCode}]";
        return ToolResult.Ok(text.Length == 0 ? summary : $"{text}\n{summary}");
    }

    /// <summary>Resolves the working directory: an explicit one (validated, then
    /// remembered for this conversation) wins; otherwise the conversation's last
    /// used directory; otherwise the user profile.</summary>
    private string ResolveWorkingDirectory(JsonElement arguments, out string? error)
    {
        error = null;
        var userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var conversationId = _session?.CurrentConversationId;

        if (ToolHelpers.GetString(arguments, "working_directory") is { Length: > 0 } wd)
        {
            var resolved = PathGuard.Resolve(wd, out var resolveError);
            if (resolved is null) { error = resolveError; return userProfile; }
            if (!Directory.Exists(resolved)) { error = $"Working directory not found: {resolved}"; return userProfile; }
            if (conversationId is not null)
                _session!.SetWorkingDirectory(conversationId, resolved);
            return resolved;
        }

        if (conversationId is not null
            && _session!.GetWorkingDirectory(conversationId) is { } saved
            && Directory.Exists(saved))
            return saved;

        return userProfile;
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
