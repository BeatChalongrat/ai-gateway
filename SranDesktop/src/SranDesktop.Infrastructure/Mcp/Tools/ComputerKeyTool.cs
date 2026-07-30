using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerKeyTool : IMcpTool, IPerceptualActionTool
{
    private readonly ScreenContext? _screen;
    private readonly ApplicationReadinessService _readiness;
    private readonly DesktopInteractionObserver _observer = new();

    public ComputerKeyTool(
        ScreenContext? screen = null,
        ApplicationReadinessService? readiness = null)
    {
        _screen = screen;
        _readiness = readiness ?? new ApplicationReadinessService();
    }

    public string Name => "computer_key";
    public string Description =>
        "Press one key/combo or an ordered key sequence against a capture-bound application " +
        "receiver. Comma strings are rejected and the expected UI state must be observed.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "keys": { "type": "string", "description": "One key or combo, e.g. Enter or Ctrl+C. Never comma-separated." },
                "sequence": { "type": "array", "items": { "type": "string" }, "description": "Ordered keys/combos." },
                "capture_id": { "type": "string", "description": "Fresh target-window capture identity." },
                "application": { "type": "string", "description": "Expected application identity." },
                "target_description": { "type": "string", "description": "Optional bound control name/id." },
                "role": { "type": "string", "description": "Expected target role." },
                "application_receiver": { "type": "boolean", "description": "Explicitly allow the verified application window to receive the key." },
                "expected_state": { "type": "string", "description": "State text/value that must be observed after the key action." },
                "timeout_ms": { "type": "integer", "minimum": 100, "maximum": 5000 }
            },
            "required": ["capture_id", "application", "expected_state"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!TryParseSequence(arguments, out var sequence, out var schemaError))
            return ToolResult.Fail(schemaError!);
        var application = ToolHelpers.GetString(arguments, "application");
        var targetDescription = ToolHelpers.GetString(arguments, "target_description");
        var role = ToolHelpers.GetString(arguments, "role");
        var expectedState = ToolHelpers.GetString(arguments, "expected_state");
        var applicationReceiver = ToolHelpers.GetBool(arguments, "application_receiver");
        if (string.IsNullOrWhiteSpace(application))
            return ToolResult.Fail("UnboundInput: application is required.");
        if (string.IsNullOrWhiteSpace(expectedState))
            return ToolResult.Fail("InvalidInputSchema: expected_state is required.");
        if (string.IsNullOrWhiteSpace(targetDescription) && !applicationReceiver)
            return ToolResult.Fail(
                "UnboundInput: target_description or application_receiver=true is required.");
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
            applicationReceiver,
            TimeSpan.FromSeconds(2),
            ct);
        if (!readiness.Ready)
            return ToolResult.Fail($"{readiness.FailureCode}: {readiness.Reason}");

        try
        {
            var hWnd = new IntPtr(capture.WindowHandle.Value);
            var root = AutomationElement.FromHandle(hWnd);
            AutomationElement? target = null;
            if (!string.IsNullOrWhiteSpace(targetDescription))
            {
                target = ApplicationReadinessService.FindTarget(
                    root, targetDescription, role, capture.ProcessId.Value);
                if (target is null)
                    return ToolResult.Fail("TargetNotFound: key receiver disappeared before dispatch.");
                target.SetFocus();
                await Task.Delay(50, ct);
            }
            if (Win32Input.GetForegroundWindow() != hWnd)
                return ToolResult.Fail("FocusLost: the bound application is not foreground.");

            var before = ReadObservableState(root);
            var accepted = true;
            var requestedEvents = 0;
            var acceptedEvents = 0;
            foreach (var key in sequence)
            {
                if (Win32Input.GetForegroundWindow() != hWnd)
                    return ToolResult.ExecutedUnverified(
                        "Key sequence stopped after receiver focus changed.",
                        "FocusLost", "Focus changed during the key sequence.", retryable: true);
                var input = Win32Input.PressKey(key);
                requestedEvents += input.RequestedInputEvents;
                acceptedEvents += input.AcceptedInputEvents;
                accepted &= input.OsInputAccepted;
                await Task.Delay(40, ct);
            }

            var timeoutMs = arguments.TryGetProperty("timeout_ms", out var timeoutValue)
                            && timeoutValue.TryGetInt32(out var parsed)
                ? Math.Clamp(parsed, 100, 5000)
                : 1000;
            var deadline = DateTimeOffset.UtcNow.AddMilliseconds(timeoutMs);
            string after;
            do
            {
                await Task.Delay(80, ct);
                after = ReadObservableState(root);
                if (!string.Equals(before, after, StringComparison.Ordinal)
                    && after.Contains(expectedState, StringComparison.OrdinalIgnoreCase))
                    break;
            } while (DateTimeOffset.UtcNow < deadline);

            var receiverConfirmed = Win32Input.GetForegroundWindow() == hWnd
                                    && (target is null
                                        || target.Current.ProcessId == capture.ProcessId.Value);
            var effectVerified = !string.Equals(before, after, StringComparison.Ordinal)
                                 && after.Contains(expectedState, StringComparison.OrdinalIgnoreCase)
                                 && receiverConfirmed;
            var identity = target is null
                ? $"application_receiver:hwnd={capture.WindowHandle.Value};pid={capture.ProcessId.Value}"
                : ApplicationReadinessService.IdentityOf(target);
            var evidence = readiness.Evidence.Concat([
                $"target_control={identity}",
                $"key_sequence_count={sequence.Count}",
                $"send_input={acceptedEvents}/{requestedEvents}",
                "receiver_confirmed=" + receiverConfirmed.ToString().ToLowerInvariant(),
                "expected_state_observed=" + effectVerified.ToString().ToLowerInvariant()
            ]).ToArray();
            var action = new ComputerActionResult(
                true, true, true, true, accepted, effectVerified, false,
                effectVerified ? null : "EffectNotObserved",
                effectVerified ? null : "The expected UI state was not observed on the bound receiver.",
                !effectVerified, evidence, capture.CaptureId, null,
                capture.WindowHandle, capture.ProcessId,
                null, null, null, null, null, null, 0,
                TargetSelectedCorrectly: true,
                TargetActivated: receiverConfirmed);
            return accepted
                ? ToolResult.FromComputerAction(
                    effectVerified
                        ? "Key action and expected receiver state were independently verified."
                        : "Key input was accepted but the expected receiver state was not verified.",
                    action)
                : ToolResult.FailedComputerAction("Windows rejected part of the key sequence.", action);
        }
        catch (ElementNotAvailableException)
        {
            return ToolResult.Fail("StaleObservation: UIA state became stale during the key action.");
        }
    }

    internal static bool TryParseSequence(
        JsonElement arguments,
        out IReadOnlyList<string> sequence,
        out string? error)
    {
        var values = new List<string>();
        if (arguments.TryGetProperty("sequence", out var sequenceElement))
        {
            if (sequenceElement.ValueKind != JsonValueKind.Array)
            {
                sequence = [];
                error = "InvalidInputSchema: sequence must be an array of key/combo strings.";
                return false;
            }
            foreach (var item in sequenceElement.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(item.GetString()))
                {
                    sequence = [];
                    error = "InvalidInputSchema: every sequence item must be a non-empty string.";
                    return false;
                }
                values.Add(item.GetString()!);
            }
        }
        else if (ToolHelpers.GetString(arguments, "keys") is { Length: > 0 } keys)
        {
            if (keys.Contains(',', StringComparison.Ordinal))
            {
                sequence = [];
                error = "InvalidInputSchema: comma-separated key text is prohibited; use sequence[].";
                return false;
            }
            values.Add(keys);
        }
        if (values.Count == 0)
        {
            sequence = [];
            error = "InvalidInputSchema: keys or sequence[] is required.";
            return false;
        }
        try
        {
            foreach (var value in values)
                Win32Input.ParseKeyCombo(value);
        }
        catch (ArgumentException ex)
        {
            sequence = [];
            error = $"InvalidInputSchema: {ex.Message}";
            return false;
        }
        sequence = values;
        error = null;
        return true;
    }

    private static string ReadObservableState(AutomationElement root)
    {
        var values = new List<string>();
        var elements = root.FindAll(TreeScope.Subtree, Condition.TrueCondition);
        foreach (AutomationElement element in elements)
        {
            try
            {
                var current = element.Current;
                if (!string.IsNullOrWhiteSpace(current.Name)) values.Add(current.Name);
                if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var value))
                    values.Add(((ValuePattern)value).Current.Value);
                if (element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle))
                    values.Add(((TogglePattern)toggle).Current.ToggleState.ToString());
            }
            catch (ElementNotAvailableException)
            {
                // Ignore one stale descendant; the root remains the receiver identity.
            }
            if (values.Sum(value => value.Length) > 16_000) break;
        }
        return string.Join("\n", values);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.KeyPress,
            $"{ToolHelpers.GetString(arguments, "application") ?? ""}:" +
            $"{ToolHelpers.GetString(arguments, "target_description") ?? "application"}",
            ToolHelpers.GetString(arguments, "expected_state") ?? "",
            SideEffectClass.ReversibleMediumRisk,
            Reversible: true,
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
