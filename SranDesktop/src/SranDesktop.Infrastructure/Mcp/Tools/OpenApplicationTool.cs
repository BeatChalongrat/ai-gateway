using System.Diagnostics;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class OpenApplicationTool : IMcpTool, IPerceptualActionTool
{
    private static readonly IReadOnlyDictionary<string, string> TrustedApplications =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["notepad"] = "notepad.exe",
            ["calculator"] = "calc.exe",
            ["calc"] = "calc.exe",
            ["paint"] = "mspaint.exe",
            ["mspaint"] = "mspaint.exe"
        };

    private readonly ApplicationReadinessService _readiness;
    private readonly DesktopInteractionObserver _observer = new();

    public OpenApplicationTool(ApplicationReadinessService? readiness = null) =>
        _readiness = readiness ?? new ApplicationReadinessService();

    public string Name => "open_application";
    public string Description =>
        "Focus an existing window by title, or launch trusted Notepad, Calculator, or Paint. " +
        "Completion is returned only after a matching HWND/PID and foreground state are verified.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "name": { "type": "string", "description": "Existing window title, or trusted app name: Notepad, Calculator, or Paint" }
            },
            "required": ["name"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var name = ToolHelpers.GetString(arguments, "name");
        if (string.IsNullOrWhiteSpace(name))
            return ToolResult.Fail("name is required.");

        var existing = Win32Input.FindWindowByTitle(name);
        if (existing != IntPtr.Zero)
        {
            if (!Win32Input.ForceForegroundWindow(existing))
                return ToolResult.Fail(
                    $"Found '{name}' at HWND {existing.ToInt64()}, but foreground focus was not verified.");
            await Task.Delay(300, ct);
            return await VerifiedWindowResultAsync(name, existing, alreadyRunning: true, ct);
        }

        if (!TryResolveTrustedApplication(name, out var executable, out var denial))
            return ToolResult.Fail(denial);

        try
        {
            var process = Process.Start(new ProcessStartInfo(executable)
            {
                UseShellExecute = false,
                CreateNoWindow = false,
                WorkingDirectory = Path.GetDirectoryName(executable)!
            });
            if (process is null)
                return ToolResult.Fail($"Could not launch trusted application '{name}'.");
        }
        catch (Exception ex)
        {
            return ToolResult.Fail(
                $"Could not launch trusted application '{name}': {ex.GetType().Name}.");
        }

        for (var waited = 0; waited < 3000; waited += 300)
        {
            await Task.Delay(300, ct);
            var hWnd = Win32Input.FindWindowByTitle(name);
            if (hWnd == IntPtr.Zero)
                continue;
            if (!Win32Input.ForceForegroundWindow(hWnd))
                return ToolResult.Fail(
                    $"Opened '{name}' at HWND {hWnd.ToInt64()}, but foreground focus was not verified.");
            return await VerifiedWindowResultAsync(name, hWnd, alreadyRunning: false, ct);
        }

        return WindowNotFoundResult(name);
    }

    internal static bool TryResolveTrustedApplication(
        string requestedName,
        out string executable,
        out string denial)
    {
        executable = string.Empty;
        denial = "Application launch denied: executable is not in the trusted application allowlist.";
        var normalized = requestedName.Trim();
        if (normalized.IndexOfAny(
                [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar, ':', '"', '\'']) >= 0
            || normalized.Any(char.IsWhiteSpace)
            || !TrustedApplications.TryGetValue(normalized, out var fileName))
            return false;

        var systemDirectory = Environment.GetFolderPath(Environment.SpecialFolder.System);
        var candidate = Path.GetFullPath(Path.Combine(systemDirectory, fileName));
        var relative = Path.GetRelativePath(systemDirectory, candidate);
        if (relative.StartsWith("..", StringComparison.Ordinal)
            || Path.IsPathRooted(relative)
            || !File.Exists(candidate))
        {
            denial = "Application launch denied: trusted executable identity is unavailable.";
            return false;
        }

        executable = candidate;
        denial = string.Empty;
        return true;
    }

    internal static ToolResult WindowNotFoundResult(string name) =>
        ToolResult.Fail(
            $"ApplicationWindowNotFound: launch was attempted, but no matching '{name}' HWND " +
            "appeared within 3 seconds. TaskCompleted=false.");

    private async Task<ToolResult> VerifiedWindowResultAsync(
        string name, IntPtr hWnd, bool alreadyRunning, CancellationToken ct)
    {
        var readiness = await _readiness.WaitForAsync(
            name,
            hWnd.ToInt64(),
            ApplicationReadinessLevel.WindowVisible,
            targetDescription: null,
            role: null,
            allowApplicationReceiver: true,
            timeout: TimeSpan.FromSeconds(3),
            ct);
        if (!readiness.Ready)
            return ToolResult.ExecutedUnverified(
                $"Application launch/focus was attempted for '{name}'.",
                readiness.FailureCode ?? "ApplicationNotReady",
                readiness.Reason,
                retryable: true,
                evidence: readiness.Evidence);
        var action = new ComputerActionResult(
            true, true, true, true, true, true, false,
            null, null, false,
            readiness.Evidence,
            null, null, readiness.WindowHandle, readiness.ProcessId,
            null, null, null, null, null, null, 0);
        var state = alreadyRunning ? "focused existing" : "opened and focused";
        return ToolResult.FromComputerAction(
            $"{state} '{name}'; actual readiness is {readiness.Level}. " +
            "Only the application-readiness outcome may be evaluated from this effect.",
            action);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.LaunchApplication,
            ToolHelpers.GetString(arguments, "name") ?? "",
            $"A foreground window matching '{ToolHelpers.GetString(arguments, "name") ?? ""}' exists.",
            SideEffectClass.ReversibleMediumRisk,
            Reversible: true,
            Idempotent: true,
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(5),
            MaximumAttempts: 2);

    public Task<InteractionObservationSnapshot> ObserveBeforeActionAsync(
        JsonElement arguments,
        CancellationToken ct) =>
        _observer.ObserveBeforeAsync(arguments, DescribeAction(arguments), ct);

    public Task<InteractionObservationSnapshot> ObserveAfterActionAsync(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        CancellationToken ct) =>
        _observer.ObserveAfterAsync(arguments, DescribeAction(arguments), before, ct);

    public ToolContractVerification VerifyAction(
        JsonElement arguments,
        ToolResult result,
        InteractionObservationSnapshot before,
        InteractionObservationSnapshot after) =>
        _observer.Verify(result, before, after);
}
