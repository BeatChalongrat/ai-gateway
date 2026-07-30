using System.Text.Json;
using System.Windows.Automation;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerDragTool : IMcpTool, IPerceptualActionTool
{
    private readonly ScreenContext _screen;
    private readonly ApplicationReadinessService _readiness;
    private readonly DesktopInteractionObserver _observer = new();

    public ComputerDragTool(
        ScreenContext screen,
        ApplicationReadinessService? readiness = null)
    {
        _screen = screen;
        _readiness = readiness ?? new ApplicationReadinessService();
    }

    public string Name => "computer_drag";
    public string Description =>
        "Drag within a capture-bound application receiver and verify the expected UI state change.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "start_x": { "type": "integer" },
                "start_y": { "type": "integer" },
                "end_x": { "type": "integer" },
                "end_y": { "type": "integer" },
                "capture_id": { "type": "string" },
                "application": { "type": "string" },
                "target_description": { "type": "string" },
                "role": { "type": "string" },
                "application_receiver": { "type": "boolean" },
                "expected_state": { "type": "string" }
            },
            "required": ["start_x", "start_y", "end_x", "end_y", "capture_id", "application", "expected_state"]
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
        if (!ComputerClickTool.TryGetScreenPoint(arguments, capture,
                out var startX, out var startY, out var error, "start_x", "start_y"))
            return ToolResult.Fail(error!);
        if (!ComputerClickTool.TryGetScreenPoint(arguments, capture,
                out var endX, out var endY, out error, "end_x", "end_y"))
            return ToolResult.Fail(error!);
        var startValidation = ComputerClickTool.ValidateLive(
            capture, startX, startY, new ComputerUseOptions());
        var endValidation = ComputerClickTool.ValidateLive(
            capture, endX, endY, new ComputerUseOptions());
        if (!startValidation.IsValid || !endValidation.IsValid)
        {
            var failed = !startValidation.IsValid ? startValidation : endValidation;
            return ToolResult.Fail($"{failed.FailureCode}: {failed.Reason}");
        }

        var readiness = await _readiness.WaitForAsync(
            application, capture.WindowHandle.Value, ApplicationReadinessLevel.InputReady,
            targetDescription, role, applicationReceiver, TimeSpan.FromSeconds(2), ct);
        if (!readiness.Ready)
            return ToolResult.Fail($"{readiness.FailureCode}: {readiness.Reason}");
        var root = AutomationElement.FromHandle(new IntPtr(capture.WindowHandle.Value));
        var before = ApplicationReadinessService.DescribeObservableState(root);
        if (Win32Input.GetForegroundWindow().ToInt64() != capture.WindowHandle.Value)
            return ToolResult.Fail("FocusLost: the bound application is not foreground.");
        await Task.Run(() => Win32Input.Drag(startX, startY, endX, endY), ct);
        await Task.Delay(100, ct);
        var after = ApplicationReadinessService.DescribeObservableState(root);
        var receiverConfirmed = Win32Input.GetForegroundWindow().ToInt64()
                                == capture.WindowHandle.Value;
        var verified = receiverConfirmed
                       && !string.Equals(before, after, StringComparison.Ordinal)
                       && after.Contains(expectedState, StringComparison.OrdinalIgnoreCase);
        var evidence = readiness.Evidence.Concat([
            $"capture_id={capture.CaptureId}", $"start={startX},{startY}",
            $"end={endX},{endY}",
            $"receiver_confirmed={receiverConfirmed.ToString().ToLowerInvariant()}",
            $"expected_state_observed={verified.ToString().ToLowerInvariant()}"
        ]).ToArray();
        var action = new ComputerActionResult(
            true, true, true, true, true, verified, false,
            verified ? null : "EffectNotObserved",
            verified ? null : "The expected drag state was not observed on the bound receiver.",
            !verified, evidence, capture.CaptureId, null, capture.WindowHandle, capture.ProcessId,
            endX, endY, null, null, null, null, 0,
            TargetSelectedCorrectly: true, TargetActivated: receiverConfirmed);
        return ToolResult.FromComputerAction(
            verified
                ? "Drag effect was independently verified on the bound receiver."
                : "Drag input was accepted but its expected effect was not verified.",
            action);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            InteractionActionKind.Drag,
            $"{ToolHelpers.GetString(arguments, "application") ?? ""}:" +
            $"{ToolHelpers.GetString(arguments, "target_description") ?? "application"}",
            ToolHelpers.GetString(arguments, "expected_state") ?? "",
            SideEffectClass.ReversibleMediumRisk,
            Reversible: true,
            Idempotent: false,
            RequiresConfirmation: true,
            Timeout: TimeSpan.FromSeconds(3),
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
