using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerScrollTool : IMcpTool, IPerceptualActionTool
{
    private readonly ScreenContext _screen;
    private readonly ApplicationReadinessService _readiness;
    private readonly DesktopInteractionObserver _observer = new();

    public ComputerScrollTool(
        ScreenContext screen,
        ApplicationReadinessService? readiness = null)
    {
        _screen = screen;
        _readiness = readiness ?? new ApplicationReadinessService();
    }

    public string Name => "computer_scroll";
    public string Description =>
        "Scroll a capture-bound application receiver and verify the expected UI state change.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowOnce;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "x": { "type": "integer" },
                "y": { "type": "integer" },
                "capture_id": { "type": "string" },
                "application": { "type": "string" },
                "target_description": { "type": "string" },
                "role": { "type": "string" },
                "application_receiver": { "type": "boolean" },
                "expected_state": { "type": "string" },
                "direction": { "type": "string", "enum": ["up", "down", "left", "right"] },
                "amount": { "type": "integer", "minimum": 1, "maximum": 10 }
            },
            "required": ["x", "y", "capture_id", "application", "expected_state"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var application = ToolHelpers.GetString(arguments, "application");
        var targetDescription = ToolHelpers.GetString(arguments, "target_description");
        var role = ToolHelpers.GetString(arguments, "role");
        var expectedState = ToolHelpers.GetString(arguments, "expected_state");
        var applicationReceiver = ToolHelpers.GetBool(arguments, "application_receiver");
        if (string.IsNullOrWhiteSpace(application) || string.IsNullOrWhiteSpace(expectedState))
            return ToolResult.Fail("UnboundInput: application and expected_state are required.");
        if (string.IsNullOrWhiteSpace(targetDescription) && !applicationReceiver)
            return ToolResult.Fail(
                "UnboundInput: target_description or application_receiver=true is required.");
        if (!ComputerClickTool.TryResolveCapture(
                arguments, _screen, out var capture, out var captureError))
            return ToolResult.Fail(captureError!);
        if (capture!.WindowHandle is null || capture.ProcessId is null)
            return ToolResult.Fail("UnboundInput: the capture has no HWND/PID identity.");
        if (!ComputerClickTool.TryGetScreenPoint(
                arguments, capture, out var x, out var y, out var error))
            return ToolResult.Fail(error!);
        var validation = ComputerClickTool.ValidateLive(
            capture, x, y, new ComputerUseOptions());
        if (!validation.IsValid)
            return ToolResult.Fail($"{validation.FailureCode}: {validation.Reason}");

        var readiness = await _readiness.WaitForAsync(
            application, capture.WindowHandle.Value, ApplicationReadinessLevel.InputReady,
            targetDescription, role, applicationReceiver, TimeSpan.FromSeconds(2), ct);
        if (!readiness.Ready)
            return ToolResult.Fail($"{readiness.FailureCode}: {readiness.Reason}");

        var root = AutomationElement.FromHandle(new IntPtr(capture.WindowHandle.Value));
        var before = ApplicationReadinessService.DescribeObservableState(root);
        var direction = ToolHelpers.GetString(arguments, "direction") ?? "down";
        var amount = arguments.TryGetProperty("amount", out var amountElement)
                     && amountElement.TryGetInt32(out var parsed)
            ? Math.Clamp(parsed, 1, 10)
            : 3;
        if (Win32Input.GetForegroundWindow().ToInt64() != capture.WindowHandle.Value)
            return ToolResult.Fail("FocusLost: the bound application is not foreground.");
        await Task.Run(() => Win32Input.Scroll(x, y, direction, amount), ct);
        await Task.Delay(100, ct);
        var after = ApplicationReadinessService.DescribeObservableState(root);
        var receiverConfirmed = Win32Input.GetForegroundWindow().ToInt64()
                                == capture.WindowHandle.Value;
        var verified = receiverConfirmed
                       && !string.Equals(before, after, StringComparison.Ordinal)
                       && after.Contains(expectedState, StringComparison.OrdinalIgnoreCase);
        var evidence = readiness.Evidence.Concat([
            $"capture_id={capture.CaptureId}", $"point={x},{y}",
            $"direction={direction}", $"amount={amount}",
            $"receiver_confirmed={receiverConfirmed.ToString().ToLowerInvariant()}",
            $"expected_state_observed={verified.ToString().ToLowerInvariant()}"
        ]).ToArray();
        var action = new ComputerActionResult(
            true, true, true, true, true, verified, false,
            verified ? null : "EffectNotObserved",
            verified ? null : "The expected scroll state was not observed on the bound receiver.",
            !verified, evidence, capture.CaptureId, null, capture.WindowHandle, capture.ProcessId,
            x, y, null, null, null, null, 0,
            TargetSelectedCorrectly: true, TargetActivated: receiverConfirmed);
        return ToolResult.FromComputerAction(
            verified
                ? "Scroll effect was independently verified on the bound receiver."
                : "Scroll input was accepted but its expected effect was not verified.",
            action);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.Scroll,
            $"{ToolHelpers.GetString(arguments, "application") ?? ""}:" +
            $"{ToolHelpers.GetString(arguments, "direction") ?? "down"}",
            ToolHelpers.GetString(arguments, "expected_state") ?? "",
            SideEffectClass.ReversibleLowRisk,
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
