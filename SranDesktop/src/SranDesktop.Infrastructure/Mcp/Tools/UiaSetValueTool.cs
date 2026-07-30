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
/// uia_set_value (UIA layer) — fills an input element from the last
/// read_screen_elements snapshot. ValuePattern.SetValue is atomic and
/// layout-independent; the fallback focuses the element and retypes
/// (select-all first so it replaces rather than appends). Shares
/// computer_type's hard credential block — same egress channel.
/// </summary>
public sealed class UiaSetValueTool : IMcpTool, IPerceptualActionTool
{
    private readonly UiaSnapshotStore _snapshots;
    private readonly DesktopInteractionObserver _observer = new();

    public UiaSetValueTool(UiaSnapshotStore snapshots) => _snapshots = snapshots;

    public string Name => "uia_set_value";
    public string Description =>
        "Set the text value of an input element by its id from read_screen_elements. " +
        "More reliable than clicking + typing. Element ids are ONLY valid from the LATEST " +
        "read_screen_elements result — never guess ids; call read_screen_elements first.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "element_id": { "type": "integer", "description": "Element id from the last read_screen_elements call" },
                "snapshot_id": { "type": "string", "description": "UIA snapshot id from read_screen_elements" },
                "application": { "type": "string", "description": "Expected application identity" },
                "text": { "type": "string", "description": "The value to set (replaces the current content)" }
            },
            "required": ["element_id", "snapshot_id", "application", "text"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!arguments.TryGetProperty("element_id", out var idEl) || !idEl.TryGetInt32(out var elementId))
            return ToolResult.Fail("element_id integer is required.");
        var text = ToolHelpers.GetString(arguments, "text");
        var snapshotId = ToolHelpers.GetString(arguments, "snapshot_id");
        var application = ToolHelpers.GetString(arguments, "application");
        if (text is null)
            return ToolResult.Fail("text is required.");
        if (string.IsNullOrWhiteSpace(snapshotId) || string.IsNullOrWhiteSpace(application))
            return ToolResult.Fail("UnboundInput: snapshot_id and application are required.");

        if (ComputerTypeTool.LooksLikeCredential(text, out var kind))
            return ToolResult.Fail(
                $"Refused: the text looks like a {kind}. Typing credentials, keys, or card " +
                "numbers is prohibited — ask the user to enter it themselves.");

        if (!_snapshots.TryGetBound(elementId, snapshotId, application,
                out var stored, out var identity, out var staleError))
            return ToolResult.Fail(staleError!);
        if (identity is null || !Win32Input.IsWindow(new IntPtr(identity.WindowHandle)))
            return ToolResult.Fail(UiaSnapshotStore.StaleError);

        try
        {
            using var agentInput = Application.Services.AgentInputScope.Enter(
                $"uia_set_value on {application}");
            var result = await Task.Run(
                    () => SetValue((AutomationElement)stored!, text, identity), ct)
                .WaitAsync(UiaToolSupport.UiaTimeout, ct);
            await Task.Delay(100, ct);
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
            return ToolResult.Fail($"uia_set_value failed: {UiaToolSupport.Describe(ex)}");
        }
    }

    private static ToolResult SetValue(
        AutomationElement element,
        string text,
        UiaSnapshotIdentity identity)
    {
        var current = element.Current; // liveness probe (stale → catch above)
        var name = current.Name ?? "";
        if (!current.IsEnabled)
            return ToolResult.Fail($"Element \"{name}\" is disabled.");
        if (current.ProcessId != identity.ProcessId)
            return ToolResult.Fail("WrongWindow: target control PID changed after observation.");

        var topHwnd = new IntPtr(identity.WindowHandle);
        // The real text-input control HWND (e.g. Win11 Notepad's RichEdit child) that
        // window-message injection and WM_GETTEXT read-back target — never the wrong
        // window: it is resolved from THIS control's snapshot window.
        var controlHwnd = topHwnd == IntPtr.Zero
            ? IntPtr.Zero
            : Win32TextInjection.FindTextInputChild(topHwnd);

        // ── Injection ladder: try each rung, verify by same-control read-back, stop on
        // the first that lands. Every rung is bound to this control's HWND/PID. ──

        // Rung 1 — UIA ValuePattern (classic Edit / WinForms / web-embedded controls).
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var pattern)
            && !((ValuePattern)pattern).Current.IsReadOnly)
        {
            ((ValuePattern)pattern).SetValue(text);
            Thread.Sleep(40);
            if (VerifyReadBack(element, controlHwnd, text, out var rb))
                return VerifiedResult(name, text, "ValuePattern", rb, controlHwnd, element, identity);
        }

        // Rung 3 (no foreground needed) — window-message injection straight to the
        // control HWND. Reaches RichEditD2DPT (Win11 Notepad), which ignores UIA
        // SetValue, and does not depend on stealing focus.
        if (controlHwnd != IntPtr.Zero
            && Win32TextInjection.ReplaceControlText(controlHwnd, text))
        {
            Thread.Sleep(80);
            if (VerifyReadBack(element, controlHwnd, text, out var rb))
                return VerifiedResult(name, text, "window_message", rb, controlHwnd, element, identity);
        }

        // The remaining rungs deliver keystrokes, so the target must be foreground.
        if (topHwnd != IntPtr.Zero)
            Win32Input.ForceForegroundWindow(topHwnd);
        var foreground = topHwnd == IntPtr.Zero
            || TryConfirmForeground(topHwnd, static () => Win32Input.GetForegroundWindow());
        element.SetFocus();
        Thread.Sleep(80);

        // Rung 2 — clipboard paste into the focused control (far more reliable than
        // per-char typing on RichEdit); the previous clipboard is restored after.
        if (foreground)
        {
            var (clipboardSet, previousClipboard) = Win32TextInjection.SetClipboardText(text);
            if (clipboardSet)
            {
                Win32Input.PressKey("Ctrl+A");
                Thread.Sleep(30);
                Win32Input.PressKey("Ctrl+V");
                Thread.Sleep(140);
                Win32TextInjection.RestoreClipboardText(previousClipboard);
                if (VerifyReadBack(element, controlHwnd, text, out var rb))
                    return VerifiedResult(name, text, "clipboard_paste", rb, controlHwnd, element, identity);
            }
        }

        // Rung 4 — SendInput Unicode typing (final fallback).
        var osAccepted = false;
        if (foreground)
        {
            Win32Input.PressKey("Ctrl+A");
            Thread.Sleep(30);
            osAccepted = Win32Input.TypeText(text).OsInputAccepted;
            Thread.Sleep(100);
            if (VerifyReadBack(element, controlHwnd, text, out var rb))
                return VerifiedResult(name, text, "sendinput_keyboard", rb, controlHwnd, element, identity);
        }

        // Delivered but not read-back-verifiable in-call (a RichEdit that yields no UIA
        // or WM_GETTEXT read-back). This is NOT success — it stays unverified with
        // HWND-bound evidence so a following read_screen_elements observation of the same
        // control can confirm it (type → observe → confirm; PART 2). No false success.
        var receiverConfirmed = foreground && element.Current.ProcessId == identity.ProcessId;
        return ToolResult.ExecutedUnverified(
            $"Delivered text to \"{name}\" but could not read it back in-call.",
            "UiaValueAwaitingObservation",
            "Text was delivered to the bound control; verify by calling read_screen_elements " +
            "on the same control (do NOT retype — re-observe to confirm).",
            retryable: true,
            evidence:
            [
                "uia_input_delivered=true",
                $"input_os_accepted={osAccepted}",
                $"foreground_confirmed={receiverConfirmed}",
                $"uia_snapshot_id={identity.SnapshotId}",
                $"hwnd={identity.WindowHandle}",
                $"control_hwnd={(long)controlHwnd}",
                $"pid={identity.ProcessId}",
                $"target_control={ApplicationReadinessService.IdentityOf(element)}"
            ]);
    }

    /// <summary>Reads the bound control back and checks it holds the intended text —
    /// via UIA (ValuePattern/TextPattern) and, when UIA gives nothing, via WM_GETTEXT on
    /// the control HWND. Both are same-control reads, so window identity is preserved.</summary>
    private static bool VerifyReadBack(
        AutomationElement element, IntPtr controlHwnd, string text, out string? readBack)
    {
        readBack = ReadValue(element, out var fromTextPattern);
        if (ValueReadBackMatches(readBack, text, allowSuffix: fromTextPattern))
            return true;
        if (controlHwnd != IntPtr.Zero)
        {
            var viaMessage = Win32TextInjection.ReadControlText(controlHwnd);
            if (!string.IsNullOrEmpty(viaMessage)
                && ValueReadBackMatches(viaMessage, text, allowSuffix: true))
            {
                readBack = viaMessage;
                return true;
            }
        }
        return false;
    }

    private static ToolResult VerifiedResult(
        string name, string text, string method, string? readBack,
        IntPtr controlHwnd, AutomationElement element, UiaSnapshotIdentity identity) =>
        ToolResult.Verified(
            $"Set and verified \"{name}\" to \"{Preview(text)}\" via {method}.",
            [$"uia_method={method}",
                // Phase 2 (evidence polish): same no_foreground_rung classification the
                // computer_type card shows, so both typing tools present the rung class
                // consistently. Evidence-only — verification never reads it.
                $"no_foreground_rung={ComputerTypeTool.IsNoForegroundRung(method).ToString().ToLowerInvariant()}",
                "value_read_back=true",
                $"read_back_value={Preview(readBack ?? "")}",
                $"uia_snapshot_id={identity.SnapshotId}",
                $"hwnd={identity.WindowHandle}", $"control_hwnd={(long)controlHwnd}",
                $"pid={identity.ProcessId}",
                $"target_control={ApplicationReadinessService.IdentityOf(element)}"]);

    private static string? ReadValue(AutomationElement element, out bool fromTextPattern)
    {
        fromTextPattern = false;
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var value))
            return ((ValuePattern)value).Current.Value;
        if (element.TryGetCurrentPattern(TextPattern.Pattern, out var text))
        {
            fromTextPattern = true;
            return ((TextPattern)text).DocumentRange.GetText(-1);
        }
        return null;
    }

    /// <summary>Compares a read-back against the intended text after trimming trailing
    /// whitespace/newlines from both (RichEdit/TextPattern documents append \r\n). For
    /// document controls (<paramref name="allowSuffix"/>) a non-empty intended text is
    /// also accepted when the normalized read-back ENDS WITH it, tolerating leading
    /// document content — the verification still reads the same bound control, so window
    /// identity is unchanged.</summary>
    internal static bool ValueReadBackMatches(string? readBack, string expected, bool allowSuffix)
    {
        if (readBack is null) return false;
        var actual = Normalize(readBack);
        var wanted = Normalize(expected);
        if (string.Equals(actual, wanted, StringComparison.Ordinal))
            return true;
        return allowSuffix && wanted.Length > 0
            && actual.EndsWith(wanted, StringComparison.Ordinal);
    }

    private static string Normalize(string value) => value.TrimEnd('\r', '\n', '\t', ' ');

    /// <summary>Retries a short window for the target HWND to become foreground; the
    /// probe is injected so it is testable without a live desktop.</summary>
    internal static bool TryConfirmForeground(
        IntPtr hWnd, Func<IntPtr> getForeground, int attempts = 3, int delayMs = 80)
    {
        for (var i = 0; i < attempts; i++)
        {
            if (getForeground() == hWnd)
                return true;
            if (i < attempts - 1)
                Thread.Sleep(delayMs);
        }
        return false;
    }

    /// <summary>Returns null when it is safe to type (target is foreground, or there is
    /// no HWND to confirm), otherwise a retryable failure so the runtime re-observes and
    /// retries rather than typing into the wrong window.</summary>
    internal static ToolResult? ForegroundGuard(IntPtr hWnd, Func<IntPtr> getForeground)
    {
        if (hWnd == IntPtr.Zero)
            return null;
        if (TryConfirmForeground(hWnd, getForeground))
            return null;
        return ToolResult.FailedEffect(
            "ForegroundNotConfirmed",
            "The target window did not become foreground, so the input was not sent " +
            "(it could have landed in the wrong window). Re-observe the screen and retry.",
            retryable: true);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.TypeText,
            $"uia-element:{BrowserToolHelpers.GetInt(arguments, "element_id")}",
            ToolHelpers.GetString(arguments, "text") ?? "",
            SideEffectClass.ReversibleLowRisk,
            Reversible: true,
            Idempotent: true,
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

    private static string Preview(string text) =>
        text.Length > 60 ? text[..60] + "…" : text;
}
