using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Uia;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// read_screen_elements (UIA layer) — walks a window's UI Automation tree and
/// returns the indexed element list uia_click / uia_set_value act on. Each
/// call REPLACES the shared snapshot: old ids describe a screen that may no
/// longer exist.
/// </summary>
public sealed class ReadScreenElementsTool : IMcpTool, ISensitiveDataTool
{
    /// <summary>Chromium/Electron only light up UIA once an accessibility
    /// client connects; the connection itself usually triggers it, but the
    /// first walk can still come back near-empty.</summary>
    private const int SparseThreshold = 2;

    private readonly UiaSnapshotStore _snapshots;

    public ReadScreenElementsTool(UiaSnapshotStore snapshots) => _snapshots = snapshots;

    public string Name => "read_screen_elements";
    public string Description =>
        "Read the interactive elements of a window. Prefer uia_click/uia_set_value with these " +
        "element ids over pixel clicking — it is far more accurate.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;
    public ToolDataFlowPolicy DataFlowPolicy { get; } = new(
        "Screen.AccessibilityReadLocal",
        "ScreenAccessibility.SendToModel",
        "accessibility_tree",
        "unknown",
        "configured model provider",
        ExternalTransmission: true,
        AlwaysRequireConfirmation: true,
        AllowPersistence: false);

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "window_title": {
                    "type": "string",
                    "description": "Optional: read the window whose title contains this text (else the foreground window)"
                }
            }
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var windowTitle = ToolHelpers.GetString(arguments, "window_title");
        var hWnd = windowTitle is { Length: > 0 }
            ? Win32Input.FindWindowByTitle(windowTitle)
            : Win32Input.GetForegroundWindow();
        if (hWnd == IntPtr.Zero)
            return ToolResult.Fail(windowTitle is { Length: > 0 }
                ? $"No visible window with a title containing '{windowTitle}'."
                : "No foreground window.");

        var title = Win32Input.GetWindowTitleText(hWnd);
        try
        {
            var (elements, infos, total) = await Task.Run(
                    () => UiaWalker.Walk(AutomationElement.FromHandle(hWnd)), ct)
                .WaitAsync(UiaToolSupport.UiaTimeout, ct);

            Win32Input.GetWindowThreadProcessId(hWnd, out var pid);
            var snapshotId = $"uia_{Guid.NewGuid():N}";
            _snapshots.Set(elements, new UiaSnapshotIdentity(
                snapshotId, hWnd.ToInt64(), checked((int)pid), title, DateTimeOffset.UtcNow));

            if (infos.Count == 0)
                return ToolResult.Verified(
                    $"Window \"{title}\": no interactive elements found. This app exposes limited " +
                    "UI Automation — fall back to computer_screenshot + computer_click.",
                    [$"uia_snapshot_id={snapshotId}", $"hwnd={hWnd.ToInt64()}",
                        $"pid={pid}", "uia_tree_observed=true"]);

            var listing = UiaFormatter.BuildListing(infos, total);
            var hint = infos.Count <= SparseThreshold
                ? "\n(this app exposes limited UI Automation — fall back to computer_screenshot + computer_click if these elements aren't enough)"
                : "";
            // Emit each control's ACTUAL value as a structured observed_value= token bound
            // to this window's hwnd (T4). This is a real post-state read, so it — and only
            // it — can satisfy a text outcome's ExpectedValue via the "type → observe →
            // confirm" loop. Values are read by the UIA walk (ValuePattern/TextPattern),
            // never echoed from any typed intent.
            var evidence = new List<string>
            {
                $"uia_snapshot_id={snapshotId}", $"hwnd={hWnd.ToInt64()}", $"pid={pid}",
                $"uia_element_count={infos.Count}", "uia_tree_observed=true"
            };
            foreach (var info in infos)
            {
                if (!string.IsNullOrEmpty(info.Value))
                    evidence.Add($"observed_value={info.Value}");
            }
            return ToolResult.Verified(
                $"UIA snapshot {snapshotId} for window \"{title}\" — {infos.Count} interactive elements:\n{listing}{hint}",
                evidence.ToArray());
        }
        catch (TimeoutException)
        {
            return ToolResult.Fail(UiaToolSupport.TimeoutError);
        }
        catch (Exception ex)
        {
            return ToolResult.Fail($"Could not read elements: {UiaToolSupport.Describe(ex)}");
        }
    }
}
