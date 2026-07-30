namespace SranDesktop.Infrastructure.Native;

public sealed record ComputerUseOptions
{
    public TimeSpan MaxCaptureAge { get; init; } = TimeSpan.FromSeconds(5);
    public int MaximumGeometryDifference { get; init; } = 2;
    public bool ForegroundWindowRequirement { get; init; } = true;
    public bool RequireTargetWindowIdentity { get; init; } = true;
    public int MaximumRetriesPerAction { get; init; } = 4;
    public int SafeClickEdgeMargin { get; init; } = 3;
}

public sealed record LiveWindowState(
    DateTimeOffset ObservedAtUtc,
    bool WindowExists,
    int? ProcessId,
    PhysicalRect? WindowRectPhysical,
    long DisplayTopologyVersion,
    long? ForegroundWindowHandle,
    long? WindowAtPointHandle,
    int HostProcessId);

public enum CaptureValidationFailure
{
    None,
    CaptureNotFound,
    CaptureStale,
    TargetIdentityMissing,
    WindowNoLongerExists,
    ProcessMismatch,
    WindowGeometryChanged,
    DisplayTopologyChanged,
    HostWindowTargeted,
    ForegroundWindowMismatch,
    PointOutsideExpectedWindow,
    PointCoveredByOtherWindow
}

public sealed record CaptureValidationResult(
    bool IsValid,
    CaptureValidationFailure FailureCode,
    string Reason)
{
    public static CaptureValidationResult Valid() =>
        new(true, CaptureValidationFailure.None, "Capture and target window validated.");

    public static CaptureValidationResult Reject(
        CaptureValidationFailure code, string reason) =>
        new(false, code, reason);
}

public static class CaptureActionValidator
{
    public static CaptureValidationResult Validate(
        CaptureContext capture,
        (int X, int Y) physicalPoint,
        LiveWindowState live,
        ComputerUseOptions options)
    {
        if (live.ObservedAtUtc - capture.CapturedAtUtc > options.MaxCaptureAge)
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.CaptureStale,
                $"Capture {capture.CaptureId} is older than {options.MaxCaptureAge.TotalMilliseconds:0} ms.");

        if (options.RequireTargetWindowIdentity
            && (capture.WindowHandle is null || capture.ProcessId is null))
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.TargetIdentityMissing,
                $"Capture {capture.CaptureId} is not bound to an HWND and PID. Capture the target window.");

        if (capture.WindowHandle is null)
            return ValidatePointInsideCapture(capture, physicalPoint);

        if (!live.WindowExists)
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.WindowNoLongerExists,
                $"HWND {capture.WindowHandle} no longer exists.");

        if (capture.ProcessId != live.ProcessId)
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.ProcessMismatch,
                $"Expected PID {capture.ProcessId}, observed PID {live.ProcessId}.");

        if (capture.ProcessId == live.HostProcessId)
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.HostWindowTargeted,
                "The target belongs to SRAN Desktop itself.");

        if (capture.DisplayTopologyVersion != live.DisplayTopologyVersion)
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.DisplayTopologyChanged,
                "Display topology changed after the capture.");

        if (live.WindowRectPhysical is not { } liveRect
            || GeometryDifference(capture.SourceRectPhysical, liveRect)
                > options.MaximumGeometryDifference)
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.WindowGeometryChanged,
                "The target window moved or resized after the capture.");

        if (options.ForegroundWindowRequirement
            && live.ForegroundWindowHandle != capture.WindowHandle)
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.ForegroundWindowMismatch,
                $"Expected foreground HWND {capture.WindowHandle}, observed {live.ForegroundWindowHandle}.");

        if (!liveRect.Contains(physicalPoint.X, physicalPoint.Y))
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.PointOutsideExpectedWindow,
                $"Mapped point ({physicalPoint.X}, {physicalPoint.Y}) is outside the expected window.");

        if (live.WindowAtPointHandle != capture.WindowHandle)
            return CaptureValidationResult.Reject(
                CaptureValidationFailure.PointCoveredByOtherWindow,
                $"HWND at point is {live.WindowAtPointHandle}, expected {capture.WindowHandle}.");

        return CaptureValidationResult.Valid();
    }

    private static CaptureValidationResult ValidatePointInsideCapture(
        CaptureContext capture, (int X, int Y) point) =>
        capture.SourceRectPhysical.Contains(point.X, point.Y)
            ? CaptureValidationResult.Valid()
            : CaptureValidationResult.Reject(
                CaptureValidationFailure.PointOutsideExpectedWindow,
                $"Mapped point ({point.X}, {point.Y}) is outside capture {capture.CaptureId}.");

    private static int GeometryDifference(PhysicalRect expected, PhysicalRect actual) =>
        new[]
        {
            Math.Abs(expected.X - actual.X),
            Math.Abs(expected.Y - actual.Y),
            Math.Abs(expected.Right - actual.Right),
            Math.Abs(expected.Bottom - actual.Bottom)
        }.Max();
}
