using System.Text.Json;
using System.Diagnostics;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Uia;
using SranDesktop.Infrastructure.Native.Perception;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerClickTool : IMcpTool, IPerceptualActionTool
{
    private readonly ScreenContext _screen;
    private readonly IForegroundTargetTracker? _foreground;
    private readonly ComputerUseOptions _options;
    private readonly ITargetAcquisitionRuntime _targets;
    private readonly ComputerUseMetrics _metrics;
    private readonly OcrSnapshotStore? _ocrSnapshots;
    private readonly VisualSnapshotStore? _visualSnapshots;
    private readonly ApplicationReadinessService _readiness = new();
    private readonly DesktopInteractionObserver _observer = new();

    public ComputerClickTool(
        ScreenContext screen,
        IForegroundTargetTracker? foreground = null,
        ComputerUseOptions? options = null,
        ITargetAcquisitionRuntime? targets = null,
        ComputerUseMetrics? metrics = null,
        OcrSnapshotStore? ocrSnapshots = null,
        VisualSnapshotStore? visualSnapshots = null)
    {
        _screen = screen;
        _foreground = foreground;
        _options = options ?? new ComputerUseOptions();
        _targets = targets ?? new WindowsTargetAcquisitionRuntime();
        _metrics = metrics ?? new ComputerUseMetrics();
        _ocrSnapshots = ocrSnapshots;
        _visualSnapshots = visualSnapshots;
    }

    public string Name => "computer_click";
    public string Description =>
        "Activate a UI target in a captured application window. STRUCTURED-FIRST: prefer " +
        "read_screen_elements + uia_click (element ids), or pass target_description here so a " +
        "UI Automation pattern (Invoke/Toggle/Select/Expand) can activate the control WITHOUT " +
        "stealing foreground. Legacy x/y image coordinates are the LAST RESORT — only for targets " +
        "with no UI Automation (canvas, games, custom-drawn controls) — and are never preferred " +
        "over structured activation.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "x": { "type": "integer", "description": "Legacy fallback X coordinate in pixels from the left edge of the last screenshot. Use with y when semantic targeting is unavailable." },
                "y": { "type": "integer", "description": "Legacy fallback Y coordinate in pixels from the top edge of the last screenshot. Use with x when semantic targeting is unavailable." },
                "capture_id": { "type": "string", "description": "Capture id returned by computer_screenshot. Omit only for legacy latest-capture calls." },
                "ocr_observation_id": { "type": "string", "description": "Short-lived observation id returned by computer_read_text." },
                "ocr_ref": { "type": "integer", "description": "OCR text ref bound to ocr_observation_id." },
                "application": { "type": "string", "description": "Expected application name." },
                "target_description": { "type": "string", "description": "Preferred semantic target such as Send, microphone, or Settings. Use with capture_id when possible." },
                "role": { "type": "string", "description": "Expected role such as button, checkbox, edit, or menuitem." },
                "action": { "type": "string", "description": "Expected action: invoke, toggle, select, or expand." },
                "desired_state": { "type": "string", "description": "Optional state that must be verified after activation." },
                "text_hints": { "type": "array", "items": { "type": "string" } },
                "context_hints": { "type": "array", "items": { "type": "string" } },
                "risk_level": { "type": "string", "enum": ["low", "medium", "high"] },
                "vision_target": {
                    "type": "object",
                    "description": "Vision fallback bounding box in capture_id image pixels.",
                    "properties": {
                        "label": { "type": "string" },
                        "role": { "type": "string" },
                        "x": { "type": "integer" },
                        "y": { "type": "integer" },
                        "width": { "type": "integer" },
                        "height": { "type": "integer" },
                        "confidence": { "type": "number" },
                        "visual_state": { "type": "string" },
                        "parent_context": { "type": "string" },
                        "neighbor_context": { "type": "string" },
                        "alternative_candidates": { "type": "array", "items": { "type": "string" } },
                        "ambiguity": { "type": "number" },
                        "expected_action": { "type": "string" }
                    },
                    "required": ["label", "role", "x", "y", "width", "height", "confidence", "alternative_candidates", "ambiguity", "expected_action"]
                },
                "button": { "type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button (default left)" },
                "click_type": { "type": "string", "enum": ["single", "double", "triple"], "description": "Default single" }
            }
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (arguments.TryGetProperty("ocr_ref", out _)
            || arguments.TryGetProperty("ocr_observation_id", out _))
            return await ExecuteOcrTargetAsync(arguments, ct);
        if (!TryResolveCapture(arguments, _screen, out var capture, out var captureError))
            return ToolResult.Fail(captureError!);

        if (ToolHelpers.GetString(arguments, "target_description") is { Length: > 0 })
            return await ExecuteSemanticAsync(arguments, capture!, ct);

        if (!TryGetScreenPoint(arguments, capture, out var x, out var y, out var error))
            return ToolResult.Fail(error!);

        // R1-click: when the caller names the app, actively raise its window and wait
        // until it is input-ready (reuses the typing InputReadyWait, polling ~100 ms) so
        // a coordinate click into a not-yet-foreground window RETRIES instead of failing
        // outright. A readiness timeout is a retryable transient (FocusLost /
        // ApplicationNotReady). Legacy coordinate calls that omit `application` keep the
        // existing ValidateLive-only behaviour.
        if (ToolHelpers.GetString(arguments, "application") is { Length: > 0 } clickApp
            && capture!.WindowHandle is { } clickHwnd)
        {
            Win32Input.ForceForegroundWindow(new IntPtr(clickHwnd));
            var readiness = await _readiness.WaitForAsync(
                clickApp, clickHwnd, ApplicationReadinessLevel.InputReady,
                targetDescription: null, role: null, allowApplicationReceiver: true,
                ComputerTypeTool.InputReadyWait, ct);
            if (!readiness.Ready)
                return ToolResult.Fail($"{readiness.FailureCode}: {readiness.Reason}");
        }

        // Refuse clicking SRAN's own window: the agent fighting its host app
        // (accidentally pressing its own Stop/Send) is never what the user
        // asked for and can wedge the very turn that is executing. Restoring
        // the recorded target makes the refusal ACTIONABLE — the very next
        // screenshot shows the right app again instead of looping on SRAN.
        var validation = ValidateLive(capture!, x, y, _options);
        if (!validation.IsValid)
        {
            if (validation.FailureCode == CaptureValidationFailure.CaptureStale)
                _metrics.Increment(ComputerUseMetric.StaleCaptureRejected);
            if (validation.FailureCode is CaptureValidationFailure.ForegroundWindowMismatch
                or CaptureValidationFailure.PointCoveredByOtherWindow
                or CaptureValidationFailure.HostWindowTargeted)
                _metrics.Increment(ComputerUseMetric.WrongWindowPrevented);
            if (validation.FailureCode == CaptureValidationFailure.HostWindowTargeted)
            {
                var restored = _foreground?.RestoreTarget() ?? false;
                return ToolResult.Fail(OwnWindowRefusal(restored));
            }

            return ToolResult.Fail(
                $"{validation.FailureCode}: {validation.Reason} " +
                "Take a fresh target-window screenshot; do not reuse this coordinate.");
        }

        var button = ToolHelpers.GetString(arguments, "button") ?? "left";
        var clickType = ToolHelpers.GetString(arguments, "click_type") ?? "single";
        var clicks = clickType switch
        {
            "double" => 2,
            "triple" => 3,
            _ => 1
        };

        // UIA-point-first (UIA-first plan, Phase 1, option ก): before injecting a synthetic
        // mouse click, try to activate the actual UI Automation element under the point via
        // its OWN pattern (Invoke/Toggle/Select/Expand). This needs NO foreground and is
        // exactly what the coordinate would have hit — killing the foreground contention
        // that produces the red X. Only for a plain single LEFT click; right/middle/double/
        // triple clicks have no pattern equivalent and go straight to the coordinate click.
        // ValidateLive above already confirmed the right, live, non-host target window, and
        // TryActivateAtPointAsync additionally binds to the capture's process, so this cannot
        // act on the host/wrong window. Falls through to Win32Input.Click when there is no
        // actionable element/pattern at the point (canvas / games / custom-drawn controls).
        if (ShouldTryPatternFirst(button, clickType))
        {
            var pointActivation = await _targets.TryActivateAtPointAsync(capture!, x, y, ct);
            if (pointActivation.TargetActivated)
            {
                _metrics.Increment(ComputerUseMetric.TargetResolved);
                _metrics.Increment(ComputerUseMetric.SemanticTarget);
                _metrics.Increment(ComputerUseMetric.FirstAttemptSuccess);
                if (pointActivation.EffectVerified)
                    _metrics.Increment(ComputerUseMetric.EffectVerified);
                var pointAction = ActionResult(
                    capture!, null, true, true, true, true, pointActivation.EffectVerified,
                    pointActivation.EffectVerified ? null : "EffectNotVerified",
                    pointActivation.EffectVerified ? null
                        : "The element at the point was activated via its UIA pattern, but the " +
                          "expected effect is NOT VERIFIED.",
                    pointActivation.Evidence.Concat(new[]
                    {
                        $"capture_id={capture!.CaptureId}",
                        "activation=uia_point_pattern",
                        "coordinate_fallback=false"
                    }).ToArray(),
                    taskCompleted: false,
                    executedX: x, executedY: y);
                return ToolResult.FromComputerAction(
                    pointActivation.EffectVerified
                        ? "Activated the element at the point via its UIA pattern (no foreground " +
                          "needed); effect verified."
                        : "Activated the element at the point via its UIA pattern (no foreground " +
                          "needed). TaskCompleted=false — observe and verify the expected state " +
                          "before reporting success.",
                    pointAction);
            }
            // No actionable element/pattern at the point → coordinate fallback below.
        }

        // Diagnostic (OsInputRejected probe): capture the true foreground at dispatch so
        // the card shows whether input was injected while the target actually held the
        // foreground (H3) vs a UIPI/integrity rejection with the target foreground (H1/H2).
        var foregroundAtDispatch = Win32Input.GetForegroundWindow().ToInt64();
        var foregroundAcquired = capture!.WindowHandle is { } targetHwnd
                                 && foregroundAtDispatch == targetHwnd;

        var stopwatch = Stopwatch.StartNew();
        var input = await Task.Run(() => Win32Input.Click(x, y, button, clicks), ct);
        await Task.Delay(100, ct); // let the UI respond
        stopwatch.Stop();

        var action = new ComputerActionResult(
            ActionRequested: true,
            TargetResolved: true,
            TargetValidated: true,
            ExecutionAttempted: true,
            OsInputAccepted: input.OsInputAccepted,
            EffectVerified: false,
            TaskCompleted: false,
            FailureCode: input.OsInputAccepted ? "EffectNotVerified" : "OsInputRejected",
            FailureReason: input.OsInputAccepted
                ? "The input was accepted by Windows, but no expected UI effect was supplied or verified."
                : $"Windows accepted {input.AcceptedInputEvents} of {input.RequestedInputEvents} input events; " +
                  $"cursor_positioned={input.CursorPositioned}, win32_error={input.LastWin32Error}.",
            Retryable: true,
            Evidence: new[]
            {
                $"capture_id={capture!.CaptureId}",
                // OsInputRejected diagnostic surfacing (visible in the card's Output):
                $"cursor_positioned={input.CursorPositioned.ToString().ToLowerInvariant()}",
                $"send_input_accepted={input.AcceptedInputEvents}",
                $"send_input_requested={input.RequestedInputEvents}",
                $"win32_error={input.LastWin32Error}",
                $"foreground_hwnd={foregroundAtDispatch}",
                $"target_hwnd={capture.WindowHandle?.ToString() ?? "null"}",
                $"foreground_acquired={foregroundAcquired.ToString().ToLowerInvariant()}"
            },
            CaptureBeforeId: capture.CaptureId,
            CaptureAfterId: null,
            WindowHandle: capture.WindowHandle,
            ProcessId: capture.ProcessId,
            ExecutedX: x,
            ExecutedY: y,
            TargetX: null,
            TargetY: null,
            TargetWidth: null,
            TargetHeight: null,
            DurationMs: stopwatch.ElapsedMilliseconds);

        return input.OsInputAccepted
            ? ToolResult.FromComputerAction(
                $"Input accepted at ({x}, {y}), but the UI effect is NOT VERIFIED. " +
                "TaskCompleted=false. Observe and verify the expected state before reporting success.",
                action)
            : ToolResult.FailedComputerAction(action.FailureReason!, action);
    }

    private async Task<ToolResult> ExecuteOcrTargetAsync(
        JsonElement arguments,
        CancellationToken ct)
    {
        var observationId = ToolHelpers.GetString(arguments, "ocr_observation_id");
        if (_ocrSnapshots is null
            || string.IsNullOrWhiteSpace(observationId)
            || !arguments.TryGetProperty("ocr_ref", out var referenceElement)
            || !referenceElement.TryGetInt32(out var reference)
            || !_ocrSnapshots.TryResolve(
                observationId,
                reference,
                out var snapshot,
                out var region)
            || snapshot is null
            || region is null)
            return ToolResult.Fail(
                "StaleOcrReference: OCR refs must come from a fresh computer_read_text observation.");

        var capture = snapshot.Capture;
        var bounds = CoordinateMapper.ToPhysical(
            capture,
            new ImageRect(region.X, region.Y, region.Width, region.Height));
        if (bounds is null)
            return ToolResult.Fail("OcrTargetGeometryInvalid.");
        var description = ToolHelpers.GetString(arguments, "target_description")
                          ?? region.Text;
        var intent = TargetIntentParser.Create(
            description,
            ToolHelpers.GetString(arguments, "application") ?? capture.WindowTitle,
            ToolHelpers.GetString(arguments, "role"),
            ToolHelpers.GetString(arguments, "action") ?? "invoke",
            ToolHelpers.GetString(arguments, "desired_state"),
            GetStringArray(arguments, "text_hints"),
            contextHints: GetStringArray(arguments, "context_hints"),
            riskLevel: ParseRisk(ToolHelpers.GetString(arguments, "risk_level")));
        var candidate = new TargetCandidate(
            TargetMethod.OcrText,
            $"{observationId}:{reference}",
            "text",
            region.Text,
            capture.WindowHandle ?? 0,
            capture.ProcessId ?? 0,
            bounds,
            region.GroundingConfidence,
            null,
            "click",
            [
                $"ocr_observation_id={observationId}",
                $"ocr_ref={reference}",
                $"capture_id={capture.CaptureId}",
                $"capture_version={snapshot.Observation.CaptureVersion}",
                $"confidence_basis={region.ConfidenceBasis}"
            ],
            snapshot.Observation.CapturedAtUtc,
            CandidateId: $"ocr:{observationId}:{reference}",
            CaptureId: capture.CaptureId,
            TextContext: region.Text);
        var (resolution, _) = TargetResolver.Resolve(
            intent,
            [candidate],
            DateTimeOffset.UtcNow,
            new TargetResolverOptions());
        if (!resolution.Resolved)
            return ToolResult.Fail(
                $"{resolution.FailureCode}: {resolution.FailureReason}");
        var point = SafeClickPointSelector.Select(candidate, [candidate]);
        if (!point.Selected)
            return ToolResult.Fail($"{point.FailureCode}: {point.FailureReason}");
        if (capture.WindowHandle is { } handle)
            Win32Input.ForceForegroundWindow(new IntPtr(handle));
        var validation = ValidateLive(capture, point.X, point.Y, _options);
        if (!validation.IsValid)
            return ToolResult.Fail(
                $"{validation.FailureCode}: {validation.Reason} Take a fresh OCR observation.");
        if (!await _targets.ValidatePointAsync(
                capture,
                intent,
                candidate,
                point.X,
                point.Y,
                ct))
            return ToolResult.Fail(
                "OcrPointMismatch: the live accessibility context at the OCR point " +
                "does not match the recognized text.");

        var input = await Task.Run(() => Win32Input.Click(point.X, point.Y), ct);
        await Task.Delay(100, ct);
        var action = ActionResult(
            capture,
            candidate,
            true,
            true,
            input.OsInputAccepted,
            input.OsInputAccepted,
            false,
            input.OsInputAccepted ? "EffectNotVerified" : "OsInputRejected",
            input.OsInputAccepted
                ? "OCR target received input; the durable post-observation must verify its effect."
                : "Windows rejected input for the OCR target.",
            candidate.ValidationEvidence.Concat([
                $"safe_point={point.X},{point.Y}",
                $"send_input={input.AcceptedInputEvents}/{input.RequestedInputEvents}",
                "grounding=windows_ocr"
            ]).ToArray(),
            false,
            point.X,
            point.Y);
        return input.OsInputAccepted
            ? ToolResult.FromComputerAction(
                "OCR-grounded input was accepted; awaiting independent postcondition verification.",
                action)
            : ToolResult.FailedComputerAction(action.FailureReason!, action);
    }

    private async Task<ToolResult> ExecuteSemanticAsync(
        JsonElement arguments, CaptureContext capture, CancellationToken ct)
    {
        if (capture.CaptureType != CaptureType.Window
            || capture.WindowHandle is null || capture.ProcessId is null)
            return ToolResult.Fail(
                "SemanticTargetRequiresWindowCapture: capture the target application window; " +
                "desktop overview and monitor captures cannot activate small controls.");

        var intent = TargetIntentParser.Create(
            ToolHelpers.GetString(arguments, "target_description")!,
            ToolHelpers.GetString(arguments, "application"),
            ToolHelpers.GetString(arguments, "role"),
            ToolHelpers.GetString(arguments, "action") ?? "invoke",
            ToolHelpers.GetString(arguments, "desired_state"),
            GetStringArray(arguments, "text_hints"),
            contextHints: GetStringArray(arguments, "context_hints"),
            riskLevel: ParseRisk(ToolHelpers.GetString(arguments, "risk_level")));
        if (!TryParseVisionTarget(arguments, capture.CaptureId, out var vision, out var visionError))
            return ToolResult.Fail(visionError!);

        TargetAcquisitionResult acquisition;
        try
        {
            acquisition = await _targets.ResolveAsync(capture, intent, vision, ct);
        }
        catch (TimeoutException)
        {
            return ToolResult.Fail("TargetResolutionTimeout: UI Automation did not respond in time.");
        }

        if (!acquisition.Resolution.Resolved || acquisition.Resolution.Target is not { } target)
        {
            var recovery = RecoveryCoordinator.Next(
                capture.CaptureId, 0, _options.MaximumRetriesPerAction);
            return ToolResult.FailedComputerAction(
                $"{acquisition.Resolution.FailureCode}: {acquisition.Resolution.FailureReason}",
                ActionResult(
                    capture, null, false, false, false, false, false,
                    acquisition.Resolution.FailureCode,
                    acquisition.Resolution.FailureReason,
                    acquisition.Trace.Concat(RecoveryTrace(recovery)).ToArray()));
        }

        _metrics.Increment(ComputerUseMetric.TargetResolved);
        _metrics.Increment(target.Method is TargetMethod.BrowserDom or TargetMethod.WindowsUia
            ? ComputerUseMetric.SemanticTarget
            : ComputerUseMetric.VisionFallback);

        if (target.Method != TargetMethod.VisionBoundingBox)
        {
            var activation = await _targets.ActivateAsync(capture, intent, target, ct);
            if (activation.TargetActivated)
                _metrics.Increment(ComputerUseMetric.FirstAttemptSuccess);
            if (activation.EffectVerified)
                _metrics.Increment(ComputerUseMetric.EffectVerified);
            if (activation.TaskCompleted)
                _metrics.Increment(ComputerUseMetric.TaskCompleted);
            var recovery = activation.TaskCompleted ? null : RecoveryCoordinator.Next(
                capture.CaptureId, 0, _options.MaximumRetriesPerAction);
            var activationEvidence = acquisition.Trace.Concat(activation.Evidence)
                .Concat(recovery is null ? Array.Empty<string>() : RecoveryTrace(recovery))
                .ToArray();
            return activation.TargetActivated
                ? ToolResult.FromComputerAction(
                    activation.TaskCompleted
                        ? $"Activated '{target.AccessibleName}' via {target.Method}; expected effect verified."
                        : $"Activated '{target.AccessibleName}' via {target.Method}, but expected effect is NOT VERIFIED.",
                    ActionResult(
                        capture, target, activation.TargetValidated,
                        activation.InputExecuted, activation.OsInputAccepted,
                        activation.TargetActivated, activation.EffectVerified,
                        activation.FailureCode, activation.FailureReason,
                        activationEvidence,
                        activation.TaskCompleted))
                : ToolResult.FailedComputerAction(
                    $"{activation.FailureCode}: {activation.FailureReason}",
                    ActionResult(
                        capture, target, activation.TargetValidated,
                        activation.InputExecuted, activation.OsInputAccepted,
                        false, false, activation.FailureCode, activation.FailureReason,
                        activationEvidence));
        }

        if (_visualSnapshots is null || vision is null)
            return ToolResult.FailedComputerAction(
                "VisualVerificationUnavailable: visual actions require the ephemeral " +
                "capture store; host execution fallback is forbidden.",
                ActionResult(
                    capture,
                    target,
                    false,
                    false,
                    false,
                    false,
                    false,
                    "VisualVerificationUnavailable",
                    "No ephemeral visual snapshot is available.",
                    acquisition.Trace));
        if (!_visualSnapshots.TryValidateLive(
                capture,
                vision.BoundingBox,
                0.92,
                out var visualReference,
                out var visualSimilarity,
                out var visualError)
            || visualReference is null)
        {
            return ToolResult.FailedComputerAction(
                visualError ?? "StaleVisualReference.",
                ActionResult(
                    capture,
                    target,
                    false,
                    false,
                    false,
                    false,
                    false,
                    "StaleVisualReference",
                    visualError ?? "The visual target could not be matched to a fresh capture.",
                    acquisition.Trace));
        }

        var point = SafeClickPointSelector.Select(
            target, acquisition.Candidates, _options.SafeClickEdgeMargin);
        if (!point.Selected)
            return ToolResult.FailedComputerAction(
                $"{point.FailureCode}: {point.FailureReason}",
                ActionResult(capture, target, true, false, false, false, false,
                    point.FailureCode, point.FailureReason, acquisition.Trace));
        var validation = ValidateLive(capture, point.X, point.Y, _options);
        if (!validation.IsValid)
        {
            if (validation.FailureCode == CaptureValidationFailure.CaptureStale)
                _metrics.Increment(ComputerUseMetric.StaleCaptureRejected);
            if (validation.FailureCode is CaptureValidationFailure.ForegroundWindowMismatch
                or CaptureValidationFailure.PointCoveredByOtherWindow)
                _metrics.Increment(ComputerUseMetric.WrongWindowPrevented);
            return ToolResult.FailedComputerAction(
                $"{validation.FailureCode}: {validation.Reason}",
                ActionResult(capture, target, false, false, false, false, false,
                    validation.FailureCode.ToString(), validation.Reason,
                    acquisition.Trace.Concat(RecoveryTrace(RecoveryCoordinator.Next(
                        capture.CaptureId, 0, _options.MaximumRetriesPerAction))).ToArray()));
        }
        var input = await Task.Run(() => Win32Input.Click(point.X, point.Y), ct);
        await Task.Delay(100, ct);
        var activated = input.OsInputAccepted;
        if (activated)
            _metrics.Increment(ComputerUseMetric.FirstAttemptSuccess);
        var result = ActionResult(
            capture, target, true, true, input.OsInputAccepted, activated, false,
            activated ? "EffectNotVerified" : "TargetActivationNotVerified",
            activated
                ? "The correct target received input, but its expected effect was not verified."
                : "Input was sent, but activation of the selected element was not verified.",
            acquisition.Trace.Concat(new[]
            {
                $"safe_point={point.X},{point.Y}",
                $"safe_point_method={point.Method}",
                $"send_input={input.AcceptedInputEvents}/{input.RequestedInputEvents}",
                $"visual_fingerprint={visualReference.VisualFingerprint}",
                $"visual_similarity={visualSimilarity:0.000}",
                "visual_snapshot_persisted=false"
            }).ToArray(),
            false,
            point.X, point.Y);
        return activated
            ? ToolResult.FromComputerAction(
                "The selected vision target received input, but TaskCompleted=false until its effect is verified.",
                result)
            : ToolResult.FailedComputerAction(result.FailureReason!, result);
    }

    private static ComputerActionResult ActionResult(
        CaptureContext capture,
        TargetCandidate? target,
        bool validated,
        bool inputExecuted,
        bool osAccepted,
        bool activated,
        bool effectVerified,
        string? failureCode,
        string? failureReason,
        IReadOnlyList<string> evidence,
        bool taskCompleted = false,
        int? executedX = null,
        int? executedY = null) =>
        new(
            true, target is not null, validated, inputExecuted, osAccepted,
            effectVerified, taskCompleted, failureCode, failureReason,
            !taskCompleted, evidence, capture.CaptureId, null,
            capture.WindowHandle, capture.ProcessId, executedX, executedY,
            target?.BoundingBox.X, target?.BoundingBox.Y,
            target?.BoundingBox.Width, target?.BoundingBox.Height, 0,
            TargetSelectedCorrectly: target is not null,
            TargetActivated: activated);

    /// <summary>UIA-point-first (Phase 1) applies ONLY to a plain single LEFT click.
    /// Right/middle clicks (context menus) and double/triple clicks have no UIA pattern
    /// equivalent and must go straight to a synthetic coordinate click.</summary>
    internal static bool ShouldTryPatternFirst(string button, string clickType) =>
        string.Equals(button, "left", StringComparison.Ordinal)
        && string.Equals(clickType, "single", StringComparison.Ordinal);

    private static TargetRiskLevel ParseRisk(string? value) =>
        value?.ToLowerInvariant() switch
        {
            "high" => TargetRiskLevel.High,
            "medium" => TargetRiskLevel.Medium,
            _ => TargetRiskLevel.Low
        };

    private static IReadOnlyList<string> RecoveryTrace(RecoveryStep step) =>
        new[]
        {
            $"retry_id={step.RetryId}",
            $"parent_action_id={step.ParentActionId}",
            $"recovery_strategy={step.Strategy}",
            $"requires_fresh_observation={step.RequiresFreshObservation}"
        };

    private static IReadOnlyList<string> GetStringArray(JsonElement args, string name)
    {
        if (!args.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.Array)
            return Array.Empty<string>();
        return value.EnumerateArray()
            .Where(v => v.ValueKind == JsonValueKind.String)
            .Select(v => v.GetString()!)
            .Where(v => !string.IsNullOrWhiteSpace(v))
            .ToArray();
    }

    internal static bool TryParseVisionTarget(
        JsonElement args,
        string captureId,
        out VisionTarget? target,
        out string? error)
    {
        target = null;
        error = null;
        if (!args.TryGetProperty("vision_target", out var value))
            return true;
        if (value.ValueKind != JsonValueKind.Object
            || ToolHelpers.GetString(value, "label") is not { Length: > 0 } label
            || ToolHelpers.GetString(value, "role") is not { Length: > 0 }
            || !TryInt(value, "x", out var x) || !TryInt(value, "y", out var y)
            || !TryInt(value, "width", out var width) || !TryInt(value, "height", out var height)
            || !value.TryGetProperty("confidence", out var confidenceElement)
            || !confidenceElement.TryGetDouble(out var confidence)
            || !value.TryGetProperty("ambiguity", out var ambiguityElement)
            || !ambiguityElement.TryGetDouble(out var ambiguity)
            || ToolHelpers.GetString(value, "expected_action") is not { Length: > 0 } action
            || !value.TryGetProperty("alternative_candidates", out var alternatives)
            || alternatives.ValueKind != JsonValueKind.Array)
        {
            error = "VisionTargetParseFailed: vision_target must contain a valid bbox, role, " +
                    "confidence, ambiguity, alternatives, and expected_action.";
            return false;
        }
        if (width <= 0 || height <= 0 || confidence is < 0 or > 1 || ambiguity is < 0 or > 1)
        {
            error = "VisionTargetValidationFailed: bbox dimensions and confidence/ambiguity are invalid.";
            return false;
        }
        var alternativeNames = alternatives.EnumerateArray()
            .Where(a => a.ValueKind == JsonValueKind.String)
            .Select(a => a.GetString()!)
            .ToArray();
        if (ambiguity > .35 && alternativeNames.Length == 0)
            alternativeNames = new[] { "model_reported_ambiguity" };
        target = new VisionTarget(
            captureId, label, new ImageRect(x, y, width, height), confidence,
            ToolHelpers.GetString(value, "visual_state"),
            alternativeNames,
            $"parent={ToolHelpers.GetString(value, "parent_context")};" +
            $"neighbor={ToolHelpers.GetString(value, "neighbor_context")}",
            action,
            ToolHelpers.GetString(args, "desired_state") ?? "");
        return true;
    }

    private static bool TryInt(JsonElement value, string name, out int result)
    {
        result = 0;
        return value.TryGetProperty(name, out var element) && element.TryGetInt32(out result);
    }

    /// <summary>Own-window refusal text — actionable when the target app could
    /// be restored (the model's obvious next step is a fresh screenshot).</summary>
    internal static string OwnWindowRefusal(bool restored) => restored
        ? "That point is over SRAN's own window. The target app has been restored to the " +
          "foreground — take a fresh screenshot and try again."
        : "That point is over SRAN's own window — clicking the host app is not allowed. " +
          "Take a fresh screenshot of the target app and use coordinates from it.";

    /// <summary>Reads x/y (last-screenshot space), transforms them to screen
    /// space via the session mapping, and validates against the VIRTUAL
    /// desktop — negative coordinates are legal (monitor left of / above the
    /// primary). Shared with scroll/drag.</summary>
    internal static bool TryGetScreenPoint(
        JsonElement args, ScreenContext screen, out int x, out int y, out string? error,
        string xName = "x", string yName = "y")
    {
        TryResolveCapture(args, screen, out var capture, out _);
        return TryGetScreenPoint(args, capture, out x, out y, out error, xName, yName);
    }

    internal static bool TryGetScreenPoint(
        JsonElement args, CaptureContext? capture, out int x, out int y, out string? error,
        string xName = "x", string yName = "y")
    {
        x = y = 0;
        error = null;
        if (args.ValueKind != JsonValueKind.Object
            || !args.TryGetProperty(xName, out var xEl) || !xEl.TryGetInt32(out var imageX)
            || !args.TryGetProperty(yName, out var yEl) || !yEl.TryGetInt32(out var imageY))
        {
            error = $"{xName} and {yName} integer coordinates are required.";
            return false;
        }

        (x, y) = ScreenContext.ToScreenFromCapture(capture, imageX, imageY);

        var rect = Win32Input.VirtualScreenBounds();
        if (!ScreenContext.InVirtualScreen(x, y, rect))
        {
            error = $"({imageX}, {imageY}) maps to screen point ({x}, {y}), outside the desktop " +
                    $"(x {rect.X}..{rect.X + rect.Width - 1}, y {rect.Y}..{rect.Y + rect.Height - 1}). " +
                    "Take a fresh screenshot and use coordinates from it.";
            return false;
        }
        return true;
    }

    internal static bool TryResolveCapture(
        JsonElement args, ScreenContext screen,
        out CaptureContext? capture, out string? error)
    {
        var captureId = ToolHelpers.GetString(args, "capture_id");
        if (captureId is { Length: > 0 })
        {
            if (screen.TryGet(captureId, out capture))
            {
                error = null;
                return true;
            }

            capture = null;
            error = $"{CaptureValidationFailure.CaptureNotFound}: capture_id '{captureId}' " +
                    "is stale or unknown. Take a fresh screenshot.";
            return false;
        }

        capture = screen.Last;
        error = capture is null
            ? $"{CaptureValidationFailure.CaptureNotFound}: take a screenshot before clicking."
            : null;
        return capture is not null;
    }

    internal static CaptureValidationResult ValidateLive(
        CaptureContext capture, int x, int y, ComputerUseOptions options)
    {
        var hWnd = capture.WindowHandle is { } handle ? new IntPtr(handle) : IntPtr.Zero;
        PhysicalRect? rect = null;
        int? pid = null;
        var exists = hWnd != IntPtr.Zero && Win32Input.IsWindow(hWnd);
        if (exists)
        {
            Win32Input.GetWindowThreadProcessId(hWnd, out var processId);
            pid = checked((int)processId);
            if (Win32Input.GetWindowRect(hWnd, out var currentRect))
                rect = new PhysicalRect(
                    currentRect.Left, currentRect.Top,
                    currentRect.Right - currentRect.Left,
                    currentRect.Bottom - currentRect.Top);
        }

        return CaptureActionValidator.Validate(
            capture,
            (x, y),
            new LiveWindowState(
                ObservedAtUtc: DateTimeOffset.UtcNow,
                WindowExists: exists,
                ProcessId: pid,
                WindowRectPhysical: rect,
                DisplayTopologyVersion: Win32Input.DisplayTopologyVersion(),
                ForegroundWindowHandle: Win32Input.GetForegroundWindow().ToInt64(),
                WindowAtPointHandle: Win32Input.RootWindowAtPoint(x, y).ToInt64(),
                HostProcessId: Environment.ProcessId),
            options);
    }

    public InteractionActionDescriptor DescribeAction(JsonElement arguments) =>
        new(
            BrowserToolHelpers.GetInt(arguments, "clicks") > 1
                ? InteractionActionKind.DoubleClick
                : ToolHelpers.GetString(arguments, "button") == "right"
                    ? InteractionActionKind.RightClick
                    : InteractionActionKind.Click,
            ToolHelpers.GetString(arguments, "target_description")
            ?? $"capture:{ToolHelpers.GetString(arguments, "capture_id") ?? "none"}",
            ToolHelpers.GetString(arguments, "desired_state") ?? "",
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
