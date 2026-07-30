namespace SranDesktop.Infrastructure.Native;

public sealed record AccuracyAttempt(
    string TestId,
    string TargetGroup,
    string UserIntent,
    string CaptureId,
    long WindowHandle,
    int ProcessId,
    IReadOnlyList<TargetCandidate> Candidates,
    string? SelectedCandidateId,
    string GroundTruthElementId,
    PhysicalRect TargetBounds,
    PhysicalRect? PredictedBounds,
    int? ExecutedX,
    int? ExecutedY,
    string? ElementAtPointId,
    string? ActivatedElementId,
    string? BeforeState,
    string? AfterState,
    bool TargetActivated,
    bool EffectVerified,
    bool TaskCompleted,
    int RetryCount,
    long LatencyMs,
    string? FailureCode,
    TargetMethod Method,
    double DpiScale,
    int MonitorCount,
    double NeighborDistance);

public sealed record AccuracyMetrics(
    string Group,
    int Attempts,
    double TargetSelectionAccuracy,
    double MeanBoundingBoxIou,
    double ClickInsideTargetRate,
    double CorrectElementActivationRate,
    double NeighborMisclickRate,
    double WrongWindowRate,
    double FirstAttemptSuccessRate,
    double EffectVerificationRate,
    double FalseSuccessRate,
    double MeanCenterError,
    double P50CenterError,
    double P95CenterError,
    double RecoverySuccessRate);

public static class AccuracyBenchmark
{
    public static IReadOnlyList<AccuracyMetrics> CalculateByTargetGroup(
        IEnumerable<AccuracyAttempt> attempts) =>
        attempts.GroupBy(a => a.TargetGroup, StringComparer.Ordinal)
            .Select(g => Calculate(g.Key, g.ToList()))
            .OrderBy(m => m.Group, StringComparer.Ordinal)
            .ToArray();

    public static IReadOnlyList<AccuracyMetrics> CalculateByMethod(
        IEnumerable<AccuracyAttempt> attempts) =>
        attempts.GroupBy(a => a.Method)
            .Select(g => Calculate(g.Key.ToString(), g.ToList()))
            .ToArray();

    public static IReadOnlyList<AccuracyMetrics> CalculateByDpi(
        IEnumerable<AccuracyAttempt> attempts) =>
        attempts.GroupBy(a => a.DpiScale)
            .Select(g => Calculate($"dpi-{g.Key:0.##}", g.ToList()))
            .ToArray();

    public static AccuracyMetrics Calculate(
        string group, IReadOnlyList<AccuracyAttempt> attempts)
    {
        if (attempts.Count == 0)
            throw new ArgumentException("At least one ground-truth attempt is required.", nameof(attempts));
        if (attempts.Any(a => string.IsNullOrWhiteSpace(a.GroundTruthElementId)))
            throw new ArgumentException("Every attempt must contain a harness ground-truth element id.");

        var errors = attempts
            .Where(a => a.ExecutedX is not null && a.ExecutedY is not null)
            .Select(a => DistanceFromCenter(a.TargetBounds, a.ExecutedX!.Value, a.ExecutedY!.Value))
            .OrderBy(v => v)
            .ToArray();
        var selectedCorrectly = attempts.Count(a =>
            string.Equals(a.SelectedCandidateId, a.GroundTruthElementId, StringComparison.Ordinal));
        var activatedCorrectly = attempts.Count(a =>
            a.TargetActivated
            && string.Equals(a.ActivatedElementId, a.GroundTruthElementId, StringComparison.Ordinal));
        var firstAttempt = attempts.Count(a =>
            a.RetryCount == 0 && a.TargetActivated
            && string.Equals(a.ActivatedElementId, a.GroundTruthElementId, StringComparison.Ordinal));
        var recovered = attempts.Where(a => a.RetryCount > 0).ToList();

        return new AccuracyMetrics(
            group,
            attempts.Count,
            Ratio(selectedCorrectly, attempts.Count),
            attempts.Where(a => a.PredictedBounds is not null)
                .Select(a => CandidateFusion.IoU(a.TargetBounds, a.PredictedBounds!))
                .DefaultIfEmpty(0).Average(),
            Ratio(attempts.Count(a => a.ExecutedX is not null && a.ExecutedY is not null
                                      && a.TargetBounds.Contains(a.ExecutedX.Value, a.ExecutedY.Value)),
                attempts.Count),
            Ratio(activatedCorrectly, attempts.Count),
            Ratio(attempts.Count(a => a.ActivatedElementId is not null
                                      && a.ActivatedElementId != a.GroundTruthElementId
                                      && a.WindowHandle != 0), attempts.Count),
            Ratio(attempts.Count(a => a.FailureCode == "WrongWindowActivated"), attempts.Count),
            Ratio(firstAttempt, attempts.Count),
            Ratio(attempts.Count(a => a.EffectVerified), attempts.Count),
            Ratio(attempts.Count(a => a.TaskCompleted
                                      && (!a.TargetActivated || !a.EffectVerified
                                          || a.ActivatedElementId != a.GroundTruthElementId)),
                attempts.Count),
            errors.Length == 0 ? 0 : errors.Average(),
            Percentile(errors, .50),
            Percentile(errors, .95),
            recovered.Count == 0 ? 0 : Ratio(
                recovered.Count(a => a.TargetActivated
                                     && a.ActivatedElementId == a.GroundTruthElementId),
                recovered.Count));
    }

    private static double Ratio(int numerator, int denominator) =>
        denominator == 0 ? 0 : numerator / (double)denominator;

    private static double DistanceFromCenter(PhysicalRect bounds, int x, int y)
    {
        var dx = x - (bounds.X + bounds.Width / 2d);
        var dy = y - (bounds.Y + bounds.Height / 2d);
        return Math.Sqrt(dx * dx + dy * dy);
    }

    private static double Percentile(IReadOnlyList<double> sorted, double percentile)
    {
        if (sorted.Count == 0)
            return 0;
        var rank = (sorted.Count - 1) * percentile;
        var lower = (int)Math.Floor(rank);
        var upper = (int)Math.Ceiling(rank);
        if (lower == upper)
            return sorted[lower];
        return sorted[lower] + (sorted[upper] - sorted[lower]) * (rank - lower);
    }
}
