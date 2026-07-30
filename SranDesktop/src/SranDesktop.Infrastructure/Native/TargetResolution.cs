namespace SranDesktop.Infrastructure.Native;

public enum TargetMethod
{
    ApplicationAdapter = 0,
    BrowserDom = 1,
    WindowsUia = 2,
    OcrText = 3,
    VerifiedShortcut = 4,
    VisionBoundingBox = 5,
    RawPixel = 6
}

public enum TargetRiskLevel
{
    Low,
    Medium,
    High
}

public sealed record TargetIntent(
    string? Application,
    string ObjectName,
    string? Role,
    string Action,
    string? DesiredState,
    IReadOnlyList<string> TextHints,
    IReadOnlyList<string> IconHints,
    IReadOnlyList<string> PositionalHints,
    IReadOnlyList<string> ContextHints,
    TargetRiskLevel RiskLevel);

public sealed record TargetCandidate(
    TargetMethod Method,
    string ElementId,
    string Role,
    string AccessibleName,
    long WindowHandle,
    int ProcessId,
    PhysicalRect BoundingBox,
    double Confidence,
    string? CurrentState,
    string SupportedAction,
    IReadOnlyList<string> ValidationEvidence,
    DateTimeOffset ResolvedAtUtc,
    bool IsEnabled = true,
    bool IsVisible = true,
    bool IsOffscreen = false,
    string? CandidateId = null,
    string? CaptureId = null,
    string? AutomationId = null,
    string? ControlType = null,
    IReadOnlyList<string>? SupportedActions = null,
    string? TextContext = null,
    string? ParentContext = null,
    string? NeighborContext = null,
    double AmbiguityScore = 0,
    TargetRiskLevel RiskLevel = TargetRiskLevel.Low,
    System.Windows.Point? ClickablePoint = null);

public sealed record CandidateScore(
    TargetCandidate Candidate,
    double SemanticScore,
    double RoleScore,
    double StateScore,
    double SourceScore,
    double ContextScore,
    double SpatialAgreementScore,
    double AmbiguityPenalty,
    double RiskPenalty,
    double FinalScore,
    IReadOnlyList<string> Reasons);

public sealed record TargetResolution(
    bool Resolved,
    TargetCandidate? Target,
    string? FailureCode,
    string? FailureReason,
    bool RequiresUserClarification);

public sealed record TargetResolverOptions
{
    public double MinimumVisionConfidence { get; init; } = 0.85;
    public TimeSpan MaximumElementAge { get; init; } = TimeSpan.FromSeconds(3);
    public bool AllowRawPixelFallback { get; init; }
    public double MinimumLowRiskScore { get; init; } = 0.62;
    public double MinimumMediumRiskScore { get; init; } = 0.75;
    public double MinimumHighRiskScore { get; init; } = 0.88;
    public double MinimumScoreGap { get; init; } = 0.08;
    public double FusionIouThreshold { get; init; } = 0.55;
}

public static class TargetResolver
{
    public static TargetResolution Resolve(
        IEnumerable<TargetCandidate> candidates,
        DateTimeOffset now,
        TargetResolverOptions? options = null)
    {
        options ??= new TargetResolverOptions();
        var valid = candidates
            .Where(c => now - c.ResolvedAtUtc <= options.MaximumElementAge)
            .Where(c => c.IsEnabled && c.IsVisible && !c.IsOffscreen)
            .Where(c => c.BoundingBox.Width > 0 && c.BoundingBox.Height > 0)
            .Where(c => c.Method != TargetMethod.VisionBoundingBox
                        || c.Confidence >= options.MinimumVisionConfidence)
            .Where(c => c.Method != TargetMethod.RawPixel
                        || options.AllowRawPixelFallback)
            .OrderBy(c => c.Method)
            .ThenByDescending(c => c.Confidence)
            .ToList();

        if (valid.Count == 0)
            return new TargetResolution(
                false, null, "TargetNotActionable",
                "No fresh, visible, enabled target passed the confidence and fallback gates.",
                false);

        var bestMethod = valid[0].Method;
        var samePriority = valid.Where(c => c.Method == bestMethod).ToList();
        if (samePriority.Count > 1)
            return new TargetResolution(
                false, null, "AmbiguousTarget",
                $"{samePriority.Count} equally preferred {bestMethod} targets matched.",
                true);

        return new TargetResolution(true, samePriority[0], null, null, false);
    }

    public static (TargetResolution Resolution, IReadOnlyList<CandidateScore> Scores) Resolve(
        TargetIntent intent,
        IEnumerable<TargetCandidate> candidates,
        DateTimeOffset now,
        TargetResolverOptions? options = null)
    {
        options ??= new TargetResolverOptions();
        var fused = CandidateFusion.Fuse(candidates, options.FusionIouThreshold);
        var scores = fused
            .Where(c => now - c.ResolvedAtUtc <= options.MaximumElementAge)
            .Where(c => c.IsEnabled && c.IsVisible && !c.IsOffscreen)
            .Where(c => c.BoundingBox.Width > 0 && c.BoundingBox.Height > 0)
            .Where(c => c.Method != TargetMethod.VisionBoundingBox
                        || c.Confidence >= options.MinimumVisionConfidence)
            .Where(c => c.Method != TargetMethod.RawPixel || options.AllowRawPixelFallback)
            .Select(c => TargetScorer.Score(intent, c))
            .OrderByDescending(s => s.FinalScore)
            .ToList();

        if (scores.Count == 0)
            return (new TargetResolution(false, null, "TargetNotActionable",
                "No fresh, enabled candidate passed the source gates.", false), scores);

        var minimum = intent.RiskLevel switch
        {
            TargetRiskLevel.High => options.MinimumHighRiskScore,
            TargetRiskLevel.Medium => options.MinimumMediumRiskScore,
            _ => options.MinimumLowRiskScore
        };
        if (scores[0].FinalScore < minimum)
            return (new TargetResolution(false, null, "TargetScoreTooLow",
                $"Best candidate score {scores[0].FinalScore:0.000} is below {minimum:0.000}.",
                false), scores);

        if (scores.Count > 1
            && scores[0].FinalScore - scores[1].FinalScore < options.MinimumScoreGap)
            return (new TargetResolution(false, null, "AmbiguousTarget",
                $"Top score gap is {scores[0].FinalScore - scores[1].FinalScore:0.000}; " +
                $"minimum is {options.MinimumScoreGap:0.000}.", true), scores);

        return (new TargetResolution(true, scores[0].Candidate, null, null, false), scores);
    }
}

public static class TargetIntentParser
{
    private static readonly Dictionary<string, string[]> Synonyms =
        new(StringComparer.OrdinalIgnoreCase)
        {
            ["send"] = new[] { "send", "submit", "ส่ง", "ส่งข้อความ" },
            ["microphone"] = new[] { "microphone", "mic", "mute", "unmute", "ไมค์", "ไมโครโฟน", "ปิดไมค์", "เปิดไมค์" },
            ["settings"] = new[] { "settings", "preferences", "options", "ตั้งค่า", "การตั้งค่า" },
            ["save"] = new[] { "save", "บันทึก" },
            ["close"] = new[] { "close", "dismiss", "ปิด" }
        };

    public static TargetIntent Create(
        string objectName,
        string? application = null,
        string? role = null,
        string action = "invoke",
        string? desiredState = null,
        IEnumerable<string>? textHints = null,
        IEnumerable<string>? iconHints = null,
        IEnumerable<string>? positionalHints = null,
        IEnumerable<string>? contextHints = null,
        TargetRiskLevel riskLevel = TargetRiskLevel.Low)
    {
        var hints = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        AddExpanded(hints, objectName);
        foreach (var hint in textHints ?? Array.Empty<string>())
            AddExpanded(hints, hint);
        return new TargetIntent(
            application, objectName, role, action, desiredState,
            hints.ToArray(),
            (iconHints ?? Array.Empty<string>()).ToArray(),
            (positionalHints ?? Array.Empty<string>()).ToArray(),
            (contextHints ?? Array.Empty<string>()).ToArray(),
            riskLevel);
    }

    private static void AddExpanded(HashSet<string> values, string value)
    {
        if (!string.IsNullOrWhiteSpace(value))
            values.Add(value.Trim());
        foreach (var pair in Synonyms)
        {
            if (pair.Key.Equals(value, StringComparison.OrdinalIgnoreCase)
                || pair.Value.Any(v => v.Equals(value, StringComparison.OrdinalIgnoreCase)))
                foreach (var synonym in pair.Value)
                    values.Add(synonym);
        }
    }
}

public static class TargetScorer
{
    public static CandidateScore Score(TargetIntent intent, TargetCandidate candidate)
    {
        var text = string.Join(" ", new[]
        {
            candidate.AccessibleName, candidate.AutomationId, candidate.TextContext,
            candidate.ParentContext, candidate.NeighborContext
        }.Where(v => !string.IsNullOrWhiteSpace(v)));
        var exact = intent.TextHints.Any(h =>
            string.Equals(candidate.AccessibleName, h, StringComparison.OrdinalIgnoreCase));
        var partial = intent.TextHints.Any(h =>
            text.Contains(h, StringComparison.OrdinalIgnoreCase)
            || !string.IsNullOrWhiteSpace(candidate.AccessibleName)
            && h.Contains(candidate.AccessibleName, StringComparison.OrdinalIgnoreCase));
        var semantic = exact ? 1d : partial ? .72 : 0;
        var role = string.IsNullOrWhiteSpace(intent.Role) ? .75
            : RoleMatches(intent.Role, candidate.Role, candidate.ControlType) ? 1 : 0;
        var state = string.IsNullOrWhiteSpace(intent.DesiredState) ? .75
            : string.Equals(intent.DesiredState, candidate.CurrentState,
                StringComparison.OrdinalIgnoreCase) ? 1 : .45;
        var source = candidate.Method switch
        {
            TargetMethod.ApplicationAdapter => 1,
            TargetMethod.BrowserDom => .98,
            TargetMethod.WindowsUia => .94,
            TargetMethod.OcrText => .78 * candidate.Confidence,
            TargetMethod.VerifiedShortcut => .82,
            TargetMethod.VisionBoundingBox => .68 * candidate.Confidence,
            _ => .35
        };
        var context = intent.ContextHints.Count == 0 ? .7
            : intent.ContextHints.Any(h => text.Contains(h, StringComparison.OrdinalIgnoreCase))
                ? 1 : .25;
        var spatial = candidate.ValidationEvidence.Any(e =>
            e.Contains("fused:", StringComparison.OrdinalIgnoreCase)) ? 1 : .65;
        var ambiguityPenalty = Math.Clamp(candidate.AmbiguityScore, 0, 1) * .18;
        var riskPenalty = intent.RiskLevel switch
        {
            TargetRiskLevel.High when candidate.Method >= TargetMethod.VisionBoundingBox => .35,
            TargetRiskLevel.Medium when candidate.Method == TargetMethod.RawPixel => .3,
            _ => 0
        };
        var final = .31 * semantic + .17 * role + .1 * state + .2 * source
                    + .1 * context + .12 * spatial - ambiguityPenalty - riskPenalty;
        return new CandidateScore(candidate, semantic, role, state, source, context,
            spatial, ambiguityPenalty, riskPenalty, Math.Clamp(final, 0, 1),
            new[]
            {
                $"semantic={semantic:0.000}", $"role={role:0.000}",
                $"state={state:0.000}", $"source={source:0.000}",
                $"context={context:0.000}", $"spatial={spatial:0.000}"
            });
    }

    private static bool RoleMatches(string expected, string actual, string? controlType)
    {
        var normalized = expected.Replace("ControlType.", "", StringComparison.OrdinalIgnoreCase);
        return normalized.Equals(actual, StringComparison.OrdinalIgnoreCase)
               || normalized.Equals(controlType, StringComparison.OrdinalIgnoreCase)
               || normalized.Equals("toggle", StringComparison.OrdinalIgnoreCase)
                  && actual is "checkbox" or "button";
    }
}

public static class CandidateFusion
{
    public static IReadOnlyList<TargetCandidate> Fuse(
        IEnumerable<TargetCandidate> candidates, double iouThreshold)
    {
        var remaining = candidates.ToList();
        var output = new List<TargetCandidate>();
        while (remaining.Count > 0)
        {
            var seed = remaining[0];
            remaining.RemoveAt(0);
            var matches = remaining.Where(c =>
                c.Method != seed.Method
                && c.WindowHandle == seed.WindowHandle
                && c.ProcessId == seed.ProcessId
                && IoU(c.BoundingBox, seed.BoundingBox) >= iouThreshold
                && SemanticallyCompatible(c, seed)).ToList();
            foreach (var match in matches)
                remaining.Remove(match);
            if (matches.Count == 0)
            {
                output.Add(seed);
                continue;
            }

            var all = new[] { seed }.Concat(matches).ToList();
            var semantic = all.OrderBy(c => c.Method).First();
            output.Add(semantic with
            {
                Confidence = all.Max(c => c.Confidence),
                ValidationEvidence = semantic.ValidationEvidence
                    .Concat(new[] { $"fused:{string.Join("+", all.Select(c => c.Method).Distinct())}" })
                    .ToArray()
            });
        }
        return output;
    }

    public static double IoU(PhysicalRect a, PhysicalRect b)
    {
        var left = Math.Max(a.X, b.X);
        var top = Math.Max(a.Y, b.Y);
        var right = Math.Min(a.Right, b.Right);
        var bottom = Math.Min(a.Bottom, b.Bottom);
        var intersection = Math.Max(0, right - left) * Math.Max(0, bottom - top);
        var union = a.Width * a.Height + b.Width * b.Height - intersection;
        return union <= 0 ? 0 : intersection / (double)union;
    }

    private static bool SemanticallyCompatible(TargetCandidate a, TargetCandidate b)
    {
        var roleCompatible = a.Role.Equals(b.Role, StringComparison.OrdinalIgnoreCase)
                             || a.Role is "visual" || b.Role is "visual";
        var labelCompatible =
            !string.IsNullOrWhiteSpace(a.AccessibleName)
            && !string.IsNullOrWhiteSpace(b.AccessibleName)
            && (a.AccessibleName.Contains(b.AccessibleName, StringComparison.OrdinalIgnoreCase)
            || b.AccessibleName.Contains(a.AccessibleName, StringComparison.OrdinalIgnoreCase))
            || TokenOverlap(a.AccessibleName, b.AccessibleName) >= .5;
        return roleCompatible && labelCompatible;
    }

    private static double TokenOverlap(string first, string second)
    {
        var a = first.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var b = second.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        return a.Count == 0 || b.Count == 0 ? 0 : a.Intersect(b).Count() / (double)Math.Min(a.Count, b.Count);
    }
}

public sealed record SafeClickPointResult(
    bool Selected,
    int X,
    int Y,
    string Method,
    string? FailureCode,
    string? FailureReason);

public static class SafeClickPointSelector
{
    public static SafeClickPointResult Select(
        TargetCandidate target,
        IEnumerable<TargetCandidate> neighbors,
        int edgeMargin = 3)
    {
        if (target.ClickablePoint is { } clickable)
        {
            var x = (int)Math.Round(clickable.X);
            var y = (int)Math.Round(clickable.Y);
            if (target.BoundingBox.Contains(x, y)
                && !neighbors.Any(n => !IsNonActionableContainer(n)
                                       && n.CandidateId != target.CandidateId
                                       && n.BoundingBox.Contains(x, y)))
                return new(true, x, y, "uia_clickable_point", null, null);
        }

        var box = target.BoundingBox;
        if (box.Width <= edgeMargin * 2 || box.Height <= edgeMargin * 2)
            return new(false, 0, 0, "none", "NoSafeClickPoint",
                "Target is too small for the configured edge margin.");
        var otherBounds = neighbors
            .Where(n => !IsNonActionableContainer(n)
                        && n.CandidateId != target.CandidateId)
            .Select(n => n.BoundingBox)
            .ToArray();
        var points = new List<(int X, int Y, double Clearance)>();
        for (var gx = 1; gx <= 5; gx++)
        for (var gy = 1; gy <= 5; gy++)
        {
            var x = box.X + edgeMargin
                    + (box.Width - edgeMargin * 2) * gx / 6;
            var y = box.Y + edgeMargin
                    + (box.Height - edgeMargin * 2) * gy / 6;
            if (otherBounds.Any(n => n.Contains(x, y)))
                continue;
            var edgeClearance = Math.Min(
                Math.Min(x - box.X, box.Right - x),
                Math.Min(y - box.Y, box.Bottom - y));
            var neighborClearance = otherBounds.Length == 0
                ? double.MaxValue
                : otherBounds.Min(n => DistanceToRect(x, y, n));
            points.Add((x, y, Math.Min(edgeClearance, neighborClearance)));
        }
        if (points.Count == 0)
            return new(false, 0, 0, "none", "NoSafeClickPoint",
                "Every interior point overlaps a neighboring candidate.");
        var selected = points.OrderByDescending(p => p.Clearance).First();
        return new(true, selected.X, selected.Y, "max_neighbor_clearance", null, null);
    }

    private static bool IsNonActionableContainer(TargetCandidate candidate) =>
        candidate.Method == TargetMethod.WindowsUia
        && (candidate.Role.Equals("pane", StringComparison.OrdinalIgnoreCase)
            || candidate.Role.Equals("window", StringComparison.OrdinalIgnoreCase)
            || candidate.Role.Equals("group", StringComparison.OrdinalIgnoreCase));

    private static double DistanceToRect(int x, int y, PhysicalRect rect)
    {
        var dx = Math.Max(rect.X - x, Math.Max(0, x - rect.Right));
        var dy = Math.Max(rect.Y - y, Math.Max(0, y - rect.Bottom));
        return Math.Sqrt(dx * dx + dy * dy);
    }
}

public sealed record VisionTarget(
    string CaptureId,
    string Label,
    ImageRect BoundingBox,
    double Confidence,
    string? VisualState,
    IReadOnlyList<string> AlternativeCandidates,
    string ReasoningSummary,
    string ExpectedAction,
    string ExpectedStateAfterAction);

public static class VisionTargetMapper
{
    public static TargetResolution Map(
        CaptureContext capture,
        VisionTarget target,
        DateTimeOffset now,
        double minimumConfidence = 0.85)
    {
        if (!string.Equals(capture.CaptureId, target.CaptureId, StringComparison.Ordinal))
            return new TargetResolution(
                false, null, "StaleVisionTarget",
                "The vision target belongs to a different capture.", false);
        if (target.Confidence < minimumConfidence)
            return new TargetResolution(
                false, null, "LowConfidence",
                $"Vision confidence {target.Confidence:0.00} is below {minimumConfidence:0.00}.", false);
        if (target.AlternativeCandidates.Count > 0)
            return new TargetResolution(
                false, null, "AmbiguousTarget",
                "Vision returned alternative candidates.", true);

        var bounds = CoordinateMapper.ToPhysical(capture, target.BoundingBox);
        if (bounds is null)
            return new TargetResolution(
                false, null, "InvalidBoundingBox",
                "Vision bounding box does not intersect the capture.", false);

        return new TargetResolution(
            true,
            new TargetCandidate(
                TargetMethod.VisionBoundingBox,
                $"{capture.CaptureId}:{target.Label}",
                "visual",
                target.Label,
                capture.WindowHandle ?? 0,
                capture.ProcessId ?? 0,
                bounds,
                target.Confidence,
                target.VisualState,
                target.ExpectedAction,
                new[] { target.ReasoningSummary },
                now),
            null, null, false);
    }

    public static (int X, int Y) SafePoint(PhysicalRect bounds, int margin = 2)
    {
        if (bounds.Width <= margin * 2 || bounds.Height <= margin * 2)
            throw new ArgumentException("Target is too small for the required safety margin.", nameof(bounds));
        return (bounds.X + bounds.Width / 2, bounds.Y + bounds.Height / 2);
    }
}
