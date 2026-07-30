using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerTypeTool : IMcpTool, IPerceptualActionTool
{
    private readonly ScreenContext? _screen;
    private readonly ApplicationReadinessService _readiness;
    private readonly DesktopInteractionObserver _observer = new();

    public ComputerTypeTool(
        ScreenContext? screen = null,
        ApplicationReadinessService? readiness = null)
    {
        _screen = screen;
        _readiness = readiness ?? new ApplicationReadinessService();
    }

    /// <summary>
    /// R1 (adaptive readiness wait): how long to wait for the target app/control to
    /// become input-ready before giving up. <see cref="ApplicationReadinessService.WaitForAsync"/>
    /// polls every 100 ms until this deadline and returns on the FIRST poll once ready,
    /// so a control that is already ready still returns immediately — this only bounds
    /// the slow case (a freshly-opened or heavy app). Replaces a hard 2 s that failed
    /// such apps outright. Classifying the timeout as retryable is deferred to R4.
    /// </summary>
    internal static readonly TimeSpan InputReadyWait = TimeSpan.FromSeconds(6);

    public string Name => "computer_type";
    public string Description =>
        "Set text in a control bound to a fresh capture, application, HWND/PID, and semantic " +
        "target. STRUCTURED-FIRST: it tries a UI Automation ValuePattern / window message (no " +
        "foreground needed) before any keystroke fallback. Prefer read_screen_elements + " +
        "uia_set_value when you already have element ids. The exact value is read back from the " +
        "same control; unbound input is rejected.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "text": { "type": "string", "description": "The exact text to set." },
                "capture_id": { "type": "string", "description": "Fresh target-window capture identity." },
                "application": { "type": "string", "description": "Expected application identity." },
                "target_description": { "type": "string", "description": "Accessible name or automation id of the writable control." },
                "role": { "type": "string", "description": "Expected UIA role; defaults to edit." }
            },
            "required": ["text", "capture_id", "application", "target_description"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var text = ToolHelpers.GetString(arguments, "text");
        if (string.IsNullOrEmpty(text))
            return ToolResult.Fail("text is required.");
        if (LooksLikeCredential(text, out var kind))
            return ToolResult.Fail(
                $"Refused: the text looks like a {kind}. Typing credentials, keys, or card " +
                "numbers is prohibited; ask the user to enter it themselves.");

        var application = ToolHelpers.GetString(arguments, "application");
        var targetDescription = ToolHelpers.GetString(arguments, "target_description");
        var role = ToolHelpers.GetString(arguments, "role") ?? "edit";
        if (string.IsNullOrWhiteSpace(application))
            return ToolResult.Fail("UnboundInput: application is required.");
        if (string.IsNullOrWhiteSpace(targetDescription))
            return ToolResult.Fail("UnboundInput: target_description is required.");
        CaptureContext? capture = null;
        string? captureError = null;
        if (_screen is null || !ComputerClickTool.TryResolveCapture(
                arguments, _screen, out capture, out captureError))
            return ToolResult.Fail(captureError ?? "UnboundInput: capture context is unavailable.");
        if (capture!.WindowHandle is null || capture.ProcessId is null)
            return ToolResult.Fail("UnboundInput: the capture has no HWND/PID identity.");

        var centerX = capture.SourceRectPhysical.X + capture.SourceRectPhysical.Width / 2;
        var centerY = capture.SourceRectPhysical.Y + capture.SourceRectPhysical.Height / 2;
        var validation = ComputerClickTool.ValidateLive(
            capture, centerX, centerY, new ComputerUseOptions());
        if (!validation.IsValid)
            return ToolResult.Fail($"{validation.FailureCode}: {validation.Reason}");

        var readiness = await _readiness.WaitForAsync(
            application,
            capture.WindowHandle.Value,
            ApplicationReadinessLevel.InputReady,
            targetDescription,
            role,
            allowApplicationReceiver: false,
            timeout: InputReadyWait,
            ct);
        if (!readiness.Ready)
            return ToolResult.Fail($"{readiness.FailureCode}: {readiness.Reason}");

        try
        {
            var root = AutomationElement.FromHandle(new IntPtr(capture.WindowHandle.Value));
            var target = ApplicationReadinessService.FindTarget(
                root, targetDescription, role, capture.ProcessId.Value);
            if (target is null)
                return ToolResult.Fail("TargetNotFound: target disappeared before dispatch.");
            var identity = ApplicationReadinessService.IdentityOf(target);
            var before = ReadValue(target);
            var topHwnd = new IntPtr(capture.WindowHandle.Value);
            var controlHwnd = Win32TextInjection.FindTextInputChild(topHwnd);
            var nativeAccepted = false;
            string? injectionMethod = null;
            string? after = null;
            var exact = false;

            // Read the bound control back via UIA, falling back to WM_GETTEXT on the
            // resolved input-control HWND (a Win11 RichEdit yields no UIA value).
            string? ReadBack()
            {
                var v = ReadValue(target);
                if (!string.IsNullOrEmpty(v)) return v;
                return controlHwnd != IntPtr.Zero
                    ? Win32TextInjection.ReadControlText(controlHwnd) : v;
            }
            bool TryVerify(string method)
            {
                nativeAccepted = true;
                injectionMethod ??= method;
                after = ReadBack();
                exact = string.Equals(after, text, StringComparison.Ordinal);
                return exact;
            }

            // R2 — injection ladder (mirrors UiaSetValueTool): every rung is bound to
            // THIS capture's HWND/PID; try in order and stop at the first that reads back
            // exactly, so one focus race or a control that ignores a single method no
            // longer fails the whole action. If a rung delivers but read-back is
            // unavailable, later keystroke rungs are still skipped (input already landed)
            // and R3′ classification below keeps it amber, not red.
            var done = false;

            // Rung 1 — UIA ValuePattern (classic Edit / WinForms / web-embedded controls).
            if (target.TryGetCurrentPattern(ValuePattern.Pattern, out var valuePattern)
                && !((ValuePattern)valuePattern).Current.IsReadOnly)
            {
                ((ValuePattern)valuePattern).SetValue(text);
                await Task.Delay(40, ct);
                done = TryVerify("value_pattern");
            }

            // Rung 2 — window-message straight to the control HWND (reaches Win11
            // RichEdit, which ignores UIA SetValue; no focus needed).
            if (!done && controlHwnd != IntPtr.Zero
                && Win32TextInjection.ReplaceControlText(controlHwnd, text))
            {
                await Task.Delay(80, ct);
                done = TryVerify("window_message");
            }

            // Keystroke rungs need foreground — force it and confirm (retries up to 3x).
            if (!done)
            {
                Win32Input.ForceForegroundWindow(topHwnd);
                var foreground = UiaSetValueTool.TryConfirmForeground(
                    topHwnd, static () => Win32Input.GetForegroundWindow());
                if (!foreground && !nativeAccepted)
                    return ToolResult.Fail(
                        "FocusLost: the expected receiver lost foreground before dispatch.");
                if (foreground)
                {
                    target.SetFocus();
                    await Task.Delay(80, ct);

                    // Rung 3 — clipboard paste (far more reliable than per-char on
                    // RichEdit); the previous clipboard is restored afterwards.
                    var (clipboardSet, previousClipboard) = Win32TextInjection.SetClipboardText(text);
                    if (clipboardSet)
                    {
                        Win32Input.PressKey("Ctrl+A");
                        await Task.Delay(30, ct);
                        Win32Input.PressKey("Ctrl+V");
                        await Task.Delay(140, ct);
                        Win32TextInjection.RestoreClipboardText(previousClipboard);
                        done = TryVerify("clipboard_paste");
                    }

                    // Rung 4 — SendInput Unicode typing (final fallback).
                    if (!done)
                    {
                        Win32Input.PressKey("Ctrl+A");
                        await Task.Delay(30, ct);
                        if (Win32Input.TypeText(text).OsInputAccepted)
                        {
                            await Task.Delay(100, ct);
                            done = TryVerify("sendinput");
                        }
                    }
                }
            }

            await Task.Delay(60, ct);
            var foregroundAfter = Win32Input.GetForegroundWindow().ToInt64();
            // Final read-back — a late paint may settle after the last rung.
            if (!exact)
            {
                after = ReadBack();
                exact = string.Equals(after, text, StringComparison.Ordinal);
            }
            var receiverConfirmed = foregroundAfter == capture.WindowHandle.Value
                                    && target.Current.ProcessId == capture.ProcessId.Value;
            var verified = exact && receiverConfirmed;

            // R3′ — distinguish "the control exposes no value to read back" from a
            // genuine value mismatch. Both stay UNVERIFIED (amber) exactly as before —
            // Success/EffectVerified are unchanged; only the marker, reason, evidence
            // token and message differ so a follow-up observation and telemetry can
            // tell the two apart. `after is null` means neither ValuePattern nor
            // TextPattern yielded a value (e.g. a Win11 Notepad RichEdit) — the input
            // was still delivered to the confirmed receiver. A non-null `after` that
            // differs is a real mismatch and keeps the "EffectMismatch" code.
            var readBackUnavailable = after is null;
            string readBackStatus;
            string? failureCode;
            string? failureReason;
            if (verified)
            {
                readBackStatus = "exact";
                failureCode = null;
                failureReason = null;
            }
            else if (receiverConfirmed && readBackUnavailable)
            {
                readBackStatus = "unavailable";
                failureCode = "ReadBackUnavailable";
                failureReason = "Input was accepted by the bound receiver, but the control " +
                    "does not expose its value for a same-call read-back; verify by observing " +
                    "the same control (read_screen_elements) — do not retype.";
            }
            else
            {
                readBackStatus = "mismatch";
                failureCode = "EffectMismatch";
                failureReason = "The value read back from the same bound receiver did not " +
                    "match the intended text.";
            }

            var evidenceList = readiness.Evidence.Concat([
                $"target_control={identity}",
                "receiver_confirmed=" + receiverConfirmed.ToString().ToLowerInvariant(),
                "os_input_accepted=" + nativeAccepted.ToString().ToLowerInvariant(),
                "injection_method=" + (injectionMethod ?? "none"),
                // Phase 2 (evidence polish): make it legible on the card WHICH class of rung
                // delivered the text — a structured no-foreground rung (UIA ValuePattern /
                // window message) vs a keystroke rung that needed foreground. Evidence-only;
                // the raw injection_method above is kept for debugging.
                "no_foreground_rung=" + IsNoForegroundRung(injectionMethod).ToString().ToLowerInvariant(),
                "value_read_back=" + exact.ToString().ToLowerInvariant(),
                "read_back_status=" + readBackStatus,
                $"before_length={before?.Length ?? 0}",
                $"after_length={after?.Length ?? 0}"
            ]).ToList();
            // Emit the actual-read token ONLY when the exact value was truly read back
            // from the same control (T4). If read-back failed, do NOT emit a value token
            // — that would let the runtime "verify" text that never landed.
            if (exact && !string.IsNullOrEmpty(after))
                evidenceList.Add($"read_back_value={after}");
            var evidence = evidenceList.ToArray();
            var action = new ComputerActionResult(
                true, true, true, true, nativeAccepted, verified, false,
                failureCode,
                failureReason,
                !exact, evidence, capture.CaptureId, null,
                capture.WindowHandle, capture.ProcessId,
                null, null, null, null, null, null, 0,
                TargetSelectedCorrectly: true,
                TargetActivated: receiverConfirmed);
            var message = readBackStatus switch
            {
                "exact" => $"Set and independently verified {text.Length} characters in the bound control.",
                "unavailable" => "Input was accepted; this control does not expose its value for " +
                    "a same-call read-back — a follow-up observation will confirm it.",
                _ => "Input was accepted but the value read back did not match the intended text."
            };
            return nativeAccepted
                ? ToolResult.FromComputerAction(message, action)
                : ToolResult.FailedComputerAction("Windows rejected target-bound text input.", action);
        }
        catch (ElementNotAvailableException)
        {
            return ToolResult.Fail(
                "StaleObservation: the target control became stale before verification.");
        }
    }

    private static string? ReadValue(AutomationElement element)
    {
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var value))
            return ((ValuePattern)value).Current.Value;
        if (element.TryGetCurrentPattern(TextPattern.Pattern, out var text))
            return ((TextPattern)text).DocumentRange.GetText(-1).TrimEnd('\r', '\n');
        return null;
    }

    internal static bool LooksLikeCredential(string text, out string kind) =>
        Application.Services.CredentialTextGuard.LooksLikeCredential(text, out kind);

    /// <summary>Phase 2 evidence classification (shared with UiaSetValueTool for cross-tool
    /// consistency): true when the delivery rung set the value WITHOUT stealing foreground —
    /// a UIA ValuePattern or a direct window message — as opposed to a keystroke rung
    /// (clipboard paste / SendInput) that needed the target foreground. Case-insensitive so it
    /// accepts either tool's method spelling ("value_pattern" / "ValuePattern"). Evidence-only:
    /// the verification pipeline reads none of this token (see SemanticOutcomeRuntime
    /// ActualReadTokens), so it never changes success/fail.</summary>
    internal static bool IsNoForegroundRung(string? injectionMethod) =>
        string.Equals(injectionMethod, "value_pattern", StringComparison.OrdinalIgnoreCase)
        || string.Equals(injectionMethod, "valuepattern", StringComparison.OrdinalIgnoreCase)
        || string.Equals(injectionMethod, "window_message", StringComparison.OrdinalIgnoreCase);

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.TypeText,
            ToolHelpers.GetString(arguments, "target_description") ?? "",
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
}
