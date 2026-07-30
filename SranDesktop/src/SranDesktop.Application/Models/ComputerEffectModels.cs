namespace SranDesktop.Application.Models;

public enum ExpectedEffectType
{
    UiaPropertyChanged,
    ToggleStateChanged,
    DomStateChanged,
    WindowOpened,
    WindowClosed,
    ForegroundWindowChanged,
    TextValueChanged,
    ElementAppeared,
    ElementDisappeared,
    VisualRegionChanged,
    ProcessStarted,
    ProcessExited,
    UrlChanged,
    NavigationCompleted
}

public sealed record ExpectedEffect(
    ExpectedEffectType Type,
    string Subject,
    string? Property,
    string? BeforeValue,
    string? ExpectedValue);

public sealed record EffectObservation(
    string CaptureId,
    ExpectedEffectType Type,
    string Subject,
    string? Property,
    string? BeforeValue,
    string? AfterValue,
    bool SubjectExists,
    double Confidence,
    IReadOnlyList<string> Evidence);

public sealed record EffectVerificationResult(
    bool EffectVerified,
    string? FailureCode,
    string Reason,
    IReadOnlyList<string> Evidence);

public static class EffectVerifier
{
    public static EffectVerificationResult Verify(
        ExpectedEffect expected,
        EffectObservation observed,
        double minimumConfidence = 0.9)
    {
        if (observed.Confidence < minimumConfidence)
            return Failed("VerificationConfidenceLow", "Observation confidence is below the verification gate.");
        if (observed.Type != expected.Type
            || !string.Equals(observed.Subject, expected.Subject, StringComparison.Ordinal))
            return Failed("VerificationSubjectMismatch", "The observation does not describe the expected subject.");

        var verified = expected.Type switch
        {
            ExpectedEffectType.WindowOpened
                or ExpectedEffectType.ElementAppeared
                or ExpectedEffectType.ProcessStarted => observed.SubjectExists,
            ExpectedEffectType.WindowClosed
                or ExpectedEffectType.ElementDisappeared
                or ExpectedEffectType.ProcessExited => !observed.SubjectExists,
            _ => !string.Equals(observed.BeforeValue, observed.AfterValue, StringComparison.Ordinal)
                 && string.Equals(observed.AfterValue, expected.ExpectedValue, StringComparison.Ordinal)
        };

        return verified
            ? new EffectVerificationResult(true, null, "Expected UI state was observed.", observed.Evidence)
            : Failed("EffectNotObserved", "The expected state was not observed after the action.");

        EffectVerificationResult Failed(string code, string reason) =>
            new(false, code, reason, observed.Evidence);
    }
}
