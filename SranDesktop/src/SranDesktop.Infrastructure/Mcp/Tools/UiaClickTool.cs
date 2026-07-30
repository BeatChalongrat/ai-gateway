using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
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
public sealed class UiaClickTool : IMcpTool, IPerceptualActionTool
{
    private readonly UiaSnapshotStore _snapshots;
    private readonly DesktopInteractionObserver _observer = new();
    private readonly ApplicationReadinessService _readiness = new();

    public UiaClickTool(UiaSnapshotStore snapshots) => _snapshots = snapshots;

    public string Name => "uia_click";
    public string Description =>
        "Click a UI element by its id from read_screen_elements. More accurate than pixel " +
        "clicking: uses the element's own automation action when available. Element ids are " +
        "ONLY valid from the LATEST read_screen_elements result — never guess or reuse ids; " +
        "call read_screen_elements first.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "element_id": { "type": "integer", "description": "Element id from the last read_screen_elements call" },
                "snapshot_id": { "type": "string", "description": "UIA snapshot id from read_screen_elements" },
                "application": { "type": "string", "description": "Expected application identity" },
                "expected_state": { "type": "string", "description": "UI state that must be observed after activation" },
                "button": { "type": "string", "enum": ["left", "right"], "description": "Default left. right always physically clicks (context menus)." }
            },
            "required": ["element_id", "snapshot_id", "application", "expected_state"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!arguments.TryGetProperty("element_id", out var idEl) || !idEl.TryGetInt32(out var elementId))
            return ToolResult.Fail("element_id integer is required.");
        var snapshotId = ToolHelpers.GetString(arguments, "snapshot_id");
        var application = ToolHelpers.GetString(arguments, "application");
        var expectedState = ToolHelpers.GetString(arguments, "expected_state");
        if (string.IsNullOrWhiteSpace(snapshotId) || string.IsNullOrWhiteSpace(application)
            || string.IsNullOrWhiteSpace(expectedState))
            return ToolResult.Fail(
                "UnboundInput: snapshot_id, application, and expected_state are required.");
        if (!_snapshots.TryGetBound(elementId, snapshotId, application,
                out var stored, out var identity, out var staleError))
            return ToolResult.Fail(staleError!);
        if (identity is null || !Win32Input.IsWindow(new IntPtr(identity.WindowHandle)))
            return ToolResult.Fail(UiaSnapshotStore.StaleError);
        var button = ToolHelpers.GetString(arguments, "button") ?? "left";

        try
        {
            // A UIA pattern invoke presses a button without any synthetic input,
            // so it has to declare itself here or it would be the one way the
            // agent could still answer a dialog meant for a person.
            using var agentInput = Application.Services.AgentInputScope.Enter(
                $"uia_click on {application}");
            var result = await Task.Run(
                    () => Click((AutomationElement)stored!, button, expectedState, identity,
                        application!, _readiness), ct)
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

    private static ToolResult Click(
        AutomationElement element,
        string button,
        string expectedState,
        UiaSnapshotIdentity identity,
        string application,
        ApplicationReadinessService readiness)
    {
        // Current-property access doubles as the liveness probe: a vanished
        // element throws ElementNotAvailableException → stale message above.
        var current = element.Current;
        var name = current.Name ?? "";
        if (!current.IsEnabled)
            return ToolResult.Fail($"Element \"{name}\" is disabled.");
        if (current.ProcessId != identity.ProcessId)
            return ToolResult.Fail("WrongWindow: target control PID changed after observation.");
        var root = AutomationElement.FromHandle(new IntPtr(identity.WindowHandle));
        var before = ApplicationReadinessService.DescribeObservableState(root);

        // A right-click has no pattern equivalent — always physical.
        if (button == "left" && TryPatternClick(
                element, out var patternUsed, out var stateVerified))
        {
            var content = $"Activated \"{name}\" via {patternUsed}.";
            Thread.Sleep(100);
            var after = ApplicationReadinessService.DescribeObservableState(root);
            var receiverConfirmed = element.Current.ProcessId == identity.ProcessId;
            stateVerified = receiverConfirmed
                            && !string.Equals(before, after, StringComparison.Ordinal)
                            && after.Contains(expectedState, StringComparison.OrdinalIgnoreCase);
            return stateVerified
                ? ToolResult.Verified(
                    content,
                    [$"uia_pattern={patternUsed}", "target_state_changed=true",
                        $"expected_state={expectedState}",
                        $"uia_snapshot_id={identity.SnapshotId}",
                        $"hwnd={identity.WindowHandle}", $"pid={identity.ProcessId}",
                        $"target_control={ApplicationReadinessService.IdentityOf(element)}"])
                : ToolResult.ExecutedUnverified(
                    content,
                    "EffectNotObserved",
                    "The UIA pattern executed but the expected receiver state was not observed.");
        }

        return PhysicalClick(element, current, name, button, application, readiness);
    }

    /// <summary>First applicable pattern wins. Order matters: Toggle before
    /// Invoke so checkboxes toggle rather than "invoke"; SelectionItem covers
    /// radio/list/tab/tree items; ExpandCollapse opens combos/menus.</summary>
    private static bool TryPatternClick(
        AutomationElement element,
        out string patternUsed,
        out bool stateVerified)
    {
        patternUsed = "";
        stateVerified = false;
        if (element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle))
        {
            var pattern = (TogglePattern)toggle;
            var before = pattern.Current.ToggleState;
            pattern.Toggle();
            stateVerified = pattern.Current.ToggleState != before;
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
            var pattern = (SelectionItemPattern)select;
            pattern.Select();
            stateVerified = pattern.Current.IsSelected;
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
            stateVerified = pattern.Current.ExpandCollapseState
                            is ExpandCollapseState.Expanded or ExpandCollapseState.Collapsed;
            patternUsed = "ExpandCollapsePattern";
            return true;
        }
        return false;
    }

    private static ToolResult PhysicalClick(
        AutomationElement element, AutomationElement.AutomationElementInformation current,
        string name, string button, string application, ApplicationReadinessService readiness)
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
            // R1 / focus-retry (click path): actively raise the window and WAIT until it
            // is input-ready (reuses the typing wait, polling ~100 ms up to the shared
            // InputReadyWait) instead of a blind 100 ms sleep. A window that cannot be
            // brought forward is a RETRYABLE FocusLost, not a blind click into nowhere.
            Win32Input.ForceForegroundWindow(hWnd);
            var ready = readiness.WaitForAsync(
                application, hWnd.ToInt64(), ApplicationReadinessLevel.InputReady,
                targetDescription: null, role: null, allowApplicationReceiver: true,
                ComputerTypeTool.InputReadyWait, CancellationToken.None).GetAwaiter().GetResult();
            if (!ready.Ready)
                return ToolResult.FailedEffect(
                    ready.FailureCode ?? "FocusLost",
                    ready.Reason ?? "The target window did not become input-ready for a physical click.",
                    retryable: true);
        }

        var x = (int)Math.Round(point.X);
        var y = (int)Math.Round(point.Y);
        Win32Input.Click(x, y, button);
        return ToolResult.ExecutedUnverified(
            $"Clicked \"{name}\" via physical {button} click at ({x}, {y}).",
            "UiaPhysicalClickNotVerified",
            "Native input was sent, but target activation and expected state require a fresh observation.");
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            ToolHelpers.GetString(arguments, "button") switch
            {
                "right" => InteractionActionKind.RightClick,
                "double" => InteractionActionKind.DoubleClick,
                _ => InteractionActionKind.Click
            },
            $"uia-element:{BrowserToolHelpers.GetInt(arguments, "element_id")}",
            ToolHelpers.GetString(arguments, "expected_state") ?? "",
            SideEffectClass.ReversibleMediumRisk,
            Reversible: false,
            Idempotent: false,
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(2),
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
