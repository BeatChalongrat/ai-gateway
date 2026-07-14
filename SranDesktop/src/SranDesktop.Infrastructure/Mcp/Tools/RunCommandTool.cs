using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class RunCommandTool : IMcpTool
{
    private static readonly TimeSpan CommandTimeout = TimeSpan.FromSeconds(30);

    // Defense-in-depth only — the consent dialog (which shows the exact
    // command) is the primary control.
    private static readonly string[] Blocklist =
    {
        "format ", "del /s", "del /f /s", "rmdir /s", "rd /s", "rm -rf", "rm -fr",
        "shutdown", "restart-computer", "mkfs", "dd if=", ":(){", "reg delete hklm",
        "cipher /w", "diskpart", "bcdedit"
    };

    private readonly ILogger<RunCommandTool> _logger;

    public RunCommandTool(ILogger<RunCommandTool> logger) => _logger = logger;

    public string Name => "run_command";
    public string Description =>
        "Execute a Windows shell command (cmd.exe) and return stdout+stderr. Times out after 30 seconds.";
    public ToolCategory Category => ToolCategory.Shell;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "command": { "type": "string", "description": "The shell command to run" },
                "working_directory": { "type": "string", "description": "Optional working directory (defaults to the user profile)" }
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

        var workingDirectory = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (ToolHelpers.GetString(arguments, "working_directory") is { Length: > 0 } wd)
        {
            var resolved = PathGuard.Resolve(wd, out var error);
            if (resolved is null)
                return ToolResult.Fail(error!);
            if (!Directory.Exists(resolved))
                return ToolResult.Fail($"Working directory not found: {resolved}");
            workingDirectory = resolved;
        }

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

        _logger.LogInformation("run_command: {Command} (cwd={Cwd})", command, workingDirectory);
        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(CommandTimeout);
        try
        {
            await process.WaitForExitAsync(timeoutCts.Token);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(entireProcessTree: true); } catch (Exception) { /* already gone */ }
            if (ct.IsCancellationRequested)
                throw;
            return ToolResult.Fail($"Command timed out after {CommandTimeout.TotalSeconds:0}s and was killed.");
        }

        string text;
        lock (output) text = output.ToString().TrimEnd();
        var summary = $"[exit code {process.ExitCode}]";
        return ToolResult.Ok(text.Length == 0 ? summary : $"{text}\n{summary}");
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
