using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Uia;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// uia_click (UIA layer) — activates an element from the last
/// read_screen_elements snapshot. Prefers the element's own automation
/// pattern (Invoke/Toggle/SelectionItem/ExpandCollapse — pixel-perfect by
/// construction); falls back to focusing the window and physically clicking
/// the element's clickable point. It ACTS on the machine → Ask.
/// </summary>
public sealed class UiaClickTool : IMcpTool
{
    private readonly UiaSnapshotStore _snapshots;

    public UiaClickTool(UiaSnapshotStore snapshots) => _snapshots = snapshots;

    public string Name => "uia_click";
    public string Description =>
        "Click a UI element by its id from read_screen_elements. More accurate than pixel " +
        "clicking: uses the element's own automation action when available.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "element_id": { "type": "integer", "description": "Element id from the last read_screen_elements call" },
                "button": { "type": "string", "enum": ["left", "right"], "description": "Default left. right always physically clicks (context menus)." }
            },
            "required": ["element_id"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!arguments.TryGetProperty("element_id", out var idEl) || !idEl.TryGetInt32(out var elementId))
            return ToolResult.Fail("element_id integer is required.");
        if (!_snapshots.TryGet(elementId, out var stored, out var staleError))
            return ToolResult.Fail(staleError!);
        var button = ToolHelpers.GetString(arguments, "button") ?? "left";

        try
        {
            var result = await Task.Run(
                    () => Click((AutomationElement)stored!, button), ct)
                .WaitAsync(UiaToolSupport.UiaTimeout, ct);
            await Task.Delay(100, ct); // let the UI respond
            return result;
        }
        catch (TimeoutException)
        {
            return ToolResult.Fail(UiaToolSupport.TimeoutError);
        }
        catch (ElementNotAvailableException)
        {
            return ToolResult.Fail(UiaSnapshotStore.StaleError);
        }
        catch (Exception ex)
        {
            return ToolResult.Fail($"uia_click failed: {UiaToolSupport.Describe(ex)}");
        }
    }

    private static ToolResult Click(AutomationElement element, string button)
    {
        // Current-property access doubles as the liveness probe: a vanished
        // element throws ElementNotAvailableException → stale message above.
        var current = element.Current;
        var name = current.Name ?? "";
        if (!current.IsEnabled)
            return ToolResult.Fail($"Element \"{name}\" is disabled.");

        // A right-click has no pattern equivalent — always physical.
        if (button == "left" && TryPatternClick(element, out var patternUsed))
            return ToolResult.Ok($"Clicked \"{name}\" via {patternUsed}. " +
                                 "Take a screenshot or re-read elements to verify the result.");

        return PhysicalClick(element, current, name, button);
    }

    /// <summary>First applicable pattern wins. Order matters: Toggle before
    /// Invoke so checkboxes toggle rather than "invoke"; SelectionItem covers
    /// radio/list/tab/tree items; ExpandCollapse opens combos/menus.</summary>
    private static bool TryPatternClick(AutomationElement element, out string patternUsed)
    {
        patternUsed = "";
        if (element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle))
        {
            ((TogglePattern)toggle).Toggle();
            patternUsed = "TogglePattern";
            return true;
        }
        if (element.TryGetCurrentPattern(InvokePattern.Pattern, out var invoke))
        {
            ((InvokePattern)invoke).Invoke();
            patternUsed = "InvokePattern";
            return true;
        }
        if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var select))
        {
            ((SelectionItemPattern)select).Select();
            patternUsed = "SelectionItemPattern";
            return true;
        }
        if (element.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out var expand))
        {
            var pattern = (ExpandCollapsePattern)expand;
            if (pattern.Current.ExpandCollapseState == ExpandCollapseState.Expanded)
                pattern.Collapse();
            else
                pattern.Expand();
            patternUsed = "ExpandCollapsePattern";
            return true;
        }
        return false;
    }

    private static ToolResult PhysicalClick(
        AutomationElement element, AutomationElement.AutomationElementInformation current,
        string name, string button)
    {
        System.Windows.Point point;
        try
        {
            point = element.GetClickablePoint();
        }
        catch (NoClickablePointException)
        {
            var bounds = current.BoundingRectangle;
            if (bounds.IsEmpty)
                return ToolResult.Fail($"Element \"{name}\" has no clickable area.");
            point = new System.Windows.Point(
                bounds.X + bounds.Width / 2, bounds.Y + bounds.Height / 2);
        }

        // Focus the owning window first so the click lands in an active app.
        var hWnd = UiaToolSupport.WindowHandleOf(element);
        if (hWnd != IntPtr.Zero)
        {
            Win32Input.ForceForegroundWindow(hWnd);
            Thread.Sleep(100);
        }

        var x = (int)Math.Round(point.X);
        var y = (int)Math.Round(point.Y);
        Win32Input.Click(x, y, button);
        return ToolResult.Ok($"Clicked \"{name}\" via physical {button} click at ({x}, {y}). " +
                             "Take a screenshot or re-read elements to verify the result.");
    }
}
