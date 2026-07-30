using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Models;

public enum InteractionObservationSource
{
    Desktop,
    Browser
}

public enum InteractionCoordinateSpace
{
    PhysicalScreenPixels,
    LogicalDesktopCoordinates,
    VirtualDesktopPixels,
    MonitorRelativePixels,
    WindowRelativePixels,
    ScreenshotPixels,
    DomViewportCssPixels,
    DocumentCssPixels
}

public enum InteractionActionKind
{
    Click,
    DoubleClick,
    RightClick,
    Hover,
    Drag,
    Scroll,
    TypeText,
    KeyPress,
    FocusWindow,
    MoveWindow,
    ResizeWindow,
    LaunchApplication,
    CloseWindow,
    Navigate,
    Submit,
    OpenTab,
    CloseTab,
    SwitchTab,
    Upload,
    Download,
    Wait
}

public sealed record DisplaySurfaceObservation(
    string SurfaceId,
    int X,
    int Y,
    int Width,
    int Height,
    double DpiScaleX,
    double DpiScaleY,
    bool Primary);

public sealed record WindowObservation(
    long WindowHandle,
    int ProcessId,
    string ProcessIdentity,
    string Title,
    int X,
    int Y,
    int Width,
    int Height,
    bool Visible,
    bool Foreground,
    bool BlockingModalPresent);

public sealed record BrowserDocumentObservation(
    string BrowserInstanceId,
    int TabId,
    string FramePath,
    long NavigationId,
    long DomGeneration,
    string Url,
    string Title,
    string ReadyState,
    int ViewportWidth,
    int ViewportHeight,
    double Zoom,
    string? ActiveElementFingerprint)
{
    public string? BridgeSessionId { get; init; }
    public string? BrowserProfileId { get; init; }
    public string? ExtensionSessionId { get; init; }
}

/// <summary>
/// Metadata-only perception checkpoint. Raw screen pixels and DOM content are
/// deliberately referenced, never embedded, so normal durable state remains
/// redaction-safe.
/// </summary>
public sealed record InteractionObservationSnapshot(
    string ObservationId,
    string? RunId,
    string? StepId,
    InteractionObservationSource Source,
    long CaptureVersion,
    DateTimeOffset TimestampUtc,
    long DisplayTopologyVersion,
    InteractionCoordinateSpace CoordinateSpace,
    IReadOnlyList<DisplaySurfaceObservation> Displays,
    WindowObservation? ActiveWindow,
    IReadOnlyList<WindowObservation> Windows,
    BrowserDocumentObservation? BrowserDocument,
    string? AccessibilityReference,
    string? VisualReference,
    string? OcrReference,
    string StateFingerprint,
    ObservationTrust TrustLevel,
    SensitiveDataClassification Sensitivity,
    string RetentionPolicy,
    bool PreconditionsSatisfied,
    IReadOnlyList<string> Preconditions)
{
    public bool ExpectedStateObserved { get; init; }
}

public sealed record InteractionElementReference(
    string ElementReferenceId,
    string ObservationId,
    long CaptureVersion,
    InteractionObservationSource Source,
    string ApplicationIdentity,
    long? WindowHandle,
    int? ProcessId,
    int? TabId,
    string? FramePath,
    long? NavigationId,
    long? DomGeneration,
    string? BackendNodeId,
    IReadOnlyList<string> SelectorCandidates,
    string Role,
    string AccessibleName,
    string Text,
    int X,
    int Y,
    int Width,
    int Height,
    string AncestorFingerprint,
    string? VisualFingerprint,
    double Confidence,
    DateTimeOffset CreatedAtUtc,
    TimeSpan MaximumAge)
{
    public string? BrowserSessionId { get; init; }
}

public sealed record InteractionActionDescriptor(
    InteractionActionKind Kind,
    string Target,
    string ExpectedState,
    SideEffectClass SideEffect,
    bool Reversible,
    bool Idempotent,
    bool RequiresConfirmation,
    TimeSpan Timeout,
    int MaximumAttempts);

public sealed record InteractionReferenceValidation(
    bool Valid,
    string? FailureCode,
    string Reason);

public static class InteractionReferenceValidator
{
    public static InteractionReferenceValidation Validate(
        InteractionElementReference reference,
        InteractionObservationSnapshot current,
        DateTimeOffset now,
        InteractionElementReference? refreshedReference = null,
        int maximumBoundsDrift = 3)
    {
        if (now - reference.CreatedAtUtc > reference.MaximumAge)
            return Reject("StaleReference", "The element reference exceeded its maximum age.");
        if (reference.Source != current.Source)
            return Reject("ObservationSourceChanged", "The observation source changed.");
        if (!string.Equals(
                reference.ObservationId,
                current.ObservationId,
                StringComparison.Ordinal))
            return Reject(
                "ObservationIdentityChanged",
                "The element reference is not bound to the current observation.");
        if (reference.Source == InteractionObservationSource.Desktop)
        {
            if (reference.CaptureVersion != current.CaptureVersion)
                return Reject("CaptureVersionChanged", "The desktop capture version changed.");
            if (reference.WindowHandle != current.ActiveWindow?.WindowHandle
                || reference.ProcessId != current.ActiveWindow?.ProcessId)
                return Reject("WindowIdentityChanged", "The target HWND/PID identity changed.");
        }
        else
        {
            var document = current.BrowserDocument;
            if (document is null
                || (!string.IsNullOrWhiteSpace(reference.BrowserSessionId)
                    && !string.Equals(
                        reference.BrowserSessionId,
                        document.BridgeSessionId,
                        StringComparison.Ordinal))
                || reference.TabId != document.TabId
                || reference.NavigationId != document.NavigationId
                || reference.DomGeneration != document.DomGeneration
                || !string.Equals(reference.FramePath, document.FramePath, StringComparison.Ordinal))
                return Reject("StaleDomReference",
                    "The tab, frame, navigation, or DOM generation changed.");
        }

        if (refreshedReference is not null)
        {
            if (!SameSemanticTarget(reference, refreshedReference))
                return Reject(
                    "TargetIdentityChanged",
                    "The freshly resolved element no longer has the same semantic identity.");
            if (BoundsDrift(reference, refreshedReference) > maximumBoundsDrift)
                return Reject(
                    "TargetGeometryChanged",
                    "The freshly resolved element moved outside the permitted bounds drift.");
        }

        return new InteractionReferenceValidation(
            true, null, "The reference is bound to the current observation.");
    }

    private static int BoundsDrift(
        InteractionElementReference reference,
        InteractionElementReference refreshed) =>
        new[]
        {
            Math.Abs(reference.X - refreshed.X),
            Math.Abs(reference.Y - refreshed.Y),
            Math.Abs(reference.Width - refreshed.Width),
            Math.Abs(reference.Height - refreshed.Height)
        }.Max();

    private static bool SameSemanticTarget(
        InteractionElementReference reference,
        InteractionElementReference refreshed) =>
        reference.Source == refreshed.Source
        && string.Equals(
            reference.ApplicationIdentity,
            refreshed.ApplicationIdentity,
            StringComparison.OrdinalIgnoreCase)
        && string.Equals(reference.Role, refreshed.Role, StringComparison.OrdinalIgnoreCase)
        && string.Equals(
            reference.AccessibleName,
            refreshed.AccessibleName,
            StringComparison.Ordinal)
        && string.Equals(
            reference.AncestorFingerprint,
            refreshed.AncestorFingerprint,
            StringComparison.Ordinal)
        && string.Equals(
            reference.BackendNodeId,
            refreshed.BackendNodeId,
            StringComparison.Ordinal);

    private static InteractionReferenceValidation Reject(string code, string reason) =>
        new(false, code, reason);
}

public sealed record InteractionRecoveryBudget(
    int MaximumAttemptsPerAction = 3,
    int MaximumRecoveryAttemptsPerRun = 12,
    int MaximumObservationsPerAction = 8,
    int MaximumIdenticalActions = 2,
    TimeSpan? MaximumWallClock = null)
{
    public TimeSpan WallClock => MaximumWallClock ?? TimeSpan.FromSeconds(30);
}

public sealed record InteractionRecoveryState(
    int ActionAttempts,
    int TotalRecoveryAttempts,
    int ObservationCount,
    int IdenticalActionCount,
    DateTimeOffset StartedAtUtc,
    IReadOnlyList<string> RecentSignatures);

public sealed record InteractionRecoveryDecision(
    bool Allowed,
    bool Reobserve,
    bool Reground,
    bool Reconcile,
    bool NeedsUser,
    string FailureCode,
    string Reason);

public static class InteractionRecoveryPolicy
{
    public static InteractionRecoveryDecision Decide(
        string failureCode,
        bool possibleSideEffect,
        string actionSignature,
        InteractionRecoveryState state,
        InteractionRecoveryBudget? budget = null)
    {
        budget ??= new InteractionRecoveryBudget();
        if (DateTimeOffset.UtcNow - state.StartedAtUtc > budget.WallClock)
            return Stop("RecoveryWallClockExceeded", "The recovery wall-clock budget is exhausted.");
        if (state.ActionAttempts >= budget.MaximumAttemptsPerAction
            || state.TotalRecoveryAttempts >= budget.MaximumRecoveryAttemptsPerRun
            || state.ObservationCount >= budget.MaximumObservationsPerAction)
            return Stop("RecoveryBudgetExceeded", "The bounded recovery budget is exhausted.");
        if (state.IdenticalActionCount >= budget.MaximumIdenticalActions
            || state.RecentSignatures.Count(item =>
                string.Equals(item, actionSignature, StringComparison.Ordinal)) >=
                budget.MaximumIdenticalActions)
            return Stop("RepeatedActionLoop", "An identical action loop was detected.");

        if (possibleSideEffect || failureCode == "UnknownOutcome")
            return new InteractionRecoveryDecision(
                true, true, false, true, true, "ReconciliationRequired",
                "A possible side effect must be reconciled before any retry.");
        if (failureCode is "StaleObservation" or "CaptureStale"
            or "StaleReference" or "StaleDomReference" or "TargetNotFound")
            return new InteractionRecoveryDecision(
                true, true, true, false, false, "ReobserveAndReground",
                "Discard the stale reference, observe again, and resolve a fresh target.");
        if (failureCode == "FocusLost")
            return new InteractionRecoveryDecision(
                true, true, false, false, false, "RefocusAndVerify",
                "Re-identify and focus the intended window, then verify focus before retry.");

        return Stop("RecoveryNotSafe", "No deterministic recovery is safe for this failure.");
    }

    private static InteractionRecoveryDecision Stop(string code, string reason) =>
        new(false, false, false, false, false, code, reason);
}
