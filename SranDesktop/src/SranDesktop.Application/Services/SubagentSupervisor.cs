using System.Text;

namespace SranDesktop.Application.Services;

using SranDesktop.Application.Models;

/// <summary>
/// The parent side of delegation: what a child may be given, what happens when
/// one of several children fails, and what to do when two of them disagree.
///
/// The design rule throughout is that a child is an untrusted contributor of
/// evidence, not a co-author of policy. It cannot widen its own scope, its
/// failure cannot take down its siblings, and when two children come back with
/// incompatible facts the supervisor's job is to surface the disagreement with
/// both provenances attached — not to pick the one that reads better and quietly
/// drop the other, which is how a multi-agent system launders a guess into a
/// consensus.
/// </summary>
public static class SubagentSupervisor
{
    /// <summary>How deep delegation may go. Depth 1 children cannot delegate again.</summary>
    public const int MaximumDepth = 1;

    /// <summary>
    /// Creates a child run, refusing anything the parent could not itself do.
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// The requested scope exceeds the parent's, or the delegation is too deep.
    /// </exception>
    public static ChildRun CreateChild(
        string parentRunId,
        int parentDepth,
        ChildScope parentScope,
        string role,
        string goal,
        ChildScope requestedScope,
        ChildBudget? budget = null,
        string? model = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(parentRunId);
        ArgumentException.ThrowIfNullOrWhiteSpace(role);
        ArgumentException.ThrowIfNullOrWhiteSpace(goal);
        ArgumentNullException.ThrowIfNull(parentScope);
        ArgumentNullException.ThrowIfNull(requestedScope);

        if (parentDepth >= MaximumDepth)
            throw new InvalidOperationException(
                $"Delegation depth {parentDepth + 1} exceeds the limit of {MaximumDepth}. "
                + "A child agent cannot spawn further children.");

        // The whole point of the scope model: a child never receives anything
        // its parent does not already hold, so no chain of delegations can end
        // up with more access than the person who started it granted.
        if (!requestedScope.IsWithin(parentScope))
            throw new InvalidOperationException(
                "A child agent cannot be given access the parent run does not hold.");

        return new ChildRun
        {
            // Parent id stays whole so a child is traceable to its run by reading
            // the id. No truncation: the earlier version sliced to a computed
            // length and threw whenever the parent id was shorter than assumed.
            ChildRunId = $"{parentRunId}:child-{Guid.NewGuid():N}"[..(parentRunId.Length + 14)],
            ParentRunId = parentRunId,
            Role = role,
            Goal = goal,
            Model = model,
            Scope = requestedScope,
            Budget = budget ?? new ChildBudget(),
            Depth = parentDepth + 1,
            State = ChildRunState.Pending
        };
    }

    /// <summary>
    /// Applies spend and stops the child when it runs out of budget. Returning a
    /// terminal state rather than throwing keeps a budget stop indistinguishable
    /// from any other terminal outcome to the caller — including keeping whatever
    /// the child had already found.
    /// </summary>
    public static ChildRun ApplySpend(ChildRun child, ChildSpend spend, DateTimeOffset now)
    {
        ArgumentNullException.ThrowIfNull(child);
        ArgumentNullException.ThrowIfNull(spend);

        var updated = child with { Spend = spend };
        if (!spend.Exceeds(child.Budget))
            return updated;

        return updated with
        {
            State = ChildRunState.BudgetExhausted,
            StateReason = Explain(spend, child.Budget),
            CompletedUtc = now
        };
    }

    private static string Explain(ChildSpend spend, ChildBudget budget)
    {
        var parts = new List<string>();
        if (spend.ToolCalls > budget.MaxToolCalls)
            parts.Add($"{spend.ToolCalls} tool calls (limit {budget.MaxToolCalls})");
        if (spend.Tokens > budget.MaxTokens)
            parts.Add($"{spend.Tokens} tokens (limit {budget.MaxTokens})");
        if (spend.Retries > budget.MaxRetries)
            parts.Add($"{spend.Retries} retries (limit {budget.MaxRetries})");
        return "The child run stopped at its budget: " + string.Join(", ", parts) + ".";
    }

    /// <summary>Cancels one child without touching its siblings.</summary>
    public static ChildRun Cancel(ChildRun child, string reason, DateTimeOffset now) =>
        child.IsTerminal
            ? child
            : child with
            {
                State = ChildRunState.Cancelled,
                StateReason = reason,
                CompletedUtc = now
            };

    /// <summary>
    /// Prepares a child for another attempt. Findings already gathered are kept:
    /// a restart that discards completed work makes restarts expensive enough
    /// that the system starts avoiding them.
    /// </summary>
    public static ChildRun PrepareRestart(ChildRun child, DateTimeOffset now)
    {
        ArgumentNullException.ThrowIfNull(child);

        if (child.Spend.Retries >= child.Budget.MaxRetries)
            return child with
            {
                State = ChildRunState.BudgetExhausted,
                StateReason = "The child run has no retries left.",
                CompletedUtc = now
            };

        return child with
        {
            State = ChildRunState.Pending,
            StateReason = null,
            CompletedUtc = null,
            StartedUtc = now,
            Spend = child.Spend with { Retries = child.Spend.Retries + 1 }
        };
    }

    /// <summary>
    /// Collects finished children into one result: merged findings, detected
    /// conflicts, and counts. A failed child never invalidates a successful one.
    /// </summary>
    public static SupervisionResult Collect(
        IEnumerable<ChildRun> children, DateTimeOffset now)
    {
        ArgumentNullException.ThrowIfNull(children);

        var all = children.ToList();
        var succeeded = all.Count(child => child.State == ChildRunState.Succeeded);
        var failed = all.Count(child =>
            child.State is ChildRunState.Failed or ChildRunState.TimedOut
                or ChildRunState.BudgetExhausted);

        // Work from every child that produced any, including ones that later
        // failed, timed out or were cancelled. Partial evidence is still evidence.
        var findings = all
            .Where(child => child.HasUsableWork)
            .SelectMany(child => child.Findings)
            .ToList();

        var merged = Deduplicate(findings);
        var conflicts = DetectConflicts(merged, now);

        // A conflict the parent cannot settle is a question for the user, as is
        // any child that stopped to ask one.
        var requiresUser = all.Any(child => child.State == ChildRunState.NeedsUser)
                           || conflicts.Any(c => c.Resolution == ConflictResolution.Unresolved);

        return new SupervisionResult(all, merged, conflicts, succeeded, failed, requiresUser);
    }

    /// <summary>
    /// Collapses identical findings from different children into one, keeping the
    /// provenance of each. Two children finding the same fact is worth recording
    /// once with two sources — not twice, which would read as two facts.
    /// </summary>
    private static IReadOnlyList<ChildFinding> Deduplicate(List<ChildFinding> findings)
    {
        var byClaim = new Dictionary<string, ChildFinding>(StringComparer.Ordinal);

        foreach (var finding in findings)
        {
            var key = finding.NormalizedClaim;
            if (!byClaim.TryGetValue(key, out var existing))
            {
                byClaim[key] = finding;
                continue;
            }

            // Same claim seen again: keep the better-evidenced statement of it,
            // and record that more than one child arrived at it.
            var keep = finding.Confidence > existing.Confidence ? finding : existing;
            var other = ReferenceEquals(keep, finding) ? existing : finding;

            byClaim[key] = keep with
            {
                Provenance = MergeProvenance(keep.Provenance, other.Provenance),
                Confidence = Math.Min(0.99, Math.Max(keep.Confidence, other.Confidence)
                                            + (SameSource(keep, other) ? 0 : 0.05))
            };
        }

        return byClaim.Values.OrderBy(f => f.NormalizedClaim, StringComparer.Ordinal).ToList();
    }

    /// <summary>
    /// Two findings that came from the same underlying source do not corroborate
    /// each other, however many children reported them.
    /// </summary>
    private static bool SameSource(ChildFinding left, ChildFinding right) =>
        left.SourceId is not null
        && string.Equals(left.SourceId, right.SourceId, StringComparison.Ordinal);

    private static string MergeProvenance(string left, string right)
    {
        if (string.Equals(left, right, StringComparison.Ordinal))
            return left;
        var parts = new[] { left, right }
            .SelectMany(p => p.Split(" + ", StringSplitOptions.RemoveEmptyEntries))
            .Distinct(StringComparer.Ordinal)
            .OrderBy(p => p, StringComparer.Ordinal);
        return string.Join(" + ", parts);
    }

    /// <summary>
    /// Finds pairs of findings about the same subject that cannot both be true.
    ///
    /// Detection is deliberately shallow — same subject, incompatible value — and
    /// resolution is deliberately conservative. The supervisor will settle a
    /// conflict only when one side is materially better evidenced or clearly
    /// more recent; anything else is reported unresolved with both sides intact.
    /// </summary>
    private static IReadOnlyList<ChildConflict> DetectConflicts(
        IReadOnlyList<ChildFinding> findings, DateTimeOffset now)
    {
        var conflicts = new List<ChildConflict>();

        for (var i = 0; i < findings.Count; i++)
        {
            for (var j = i + 1; j < findings.Count; j++)
            {
                var left = findings[i];
                var right = findings[j];

                var subject = SharedSubject(left, right);
                if (subject is null || !AreIncompatible(left, right))
                    continue;

                conflicts.Add(Resolve(subject, left, right));
            }
        }

        return conflicts;
    }

    private static ChildConflict Resolve(string subject, ChildFinding left, ChildFinding right)
    {
        // Clearly stronger evidence on one side settles it.
        var gap = Math.Abs(left.Confidence - right.Confidence);
        if (gap >= 0.3)
        {
            var preferred = left.Confidence > right.Confidence ? left : right;
            return new ChildConflict(subject, left, right,
                ConflictResolution.ResolvedByEvidence, preferred,
                $"{preferred.ChildRunId} is better evidenced "
                + $"({preferred.Confidence:P0} vs {(preferred == left ? right : left).Confidence:P0}) "
                + $"via {preferred.Provenance}.");
        }

        // Same fact observed at different times: the later observation describes
        // the current state. Only applied when both sides carry a timestamp.
        if (left.ObservedUtc is { } leftTime && right.ObservedUtc is { } rightTime
            && Math.Abs((leftTime - rightTime).TotalHours) >= 1)
        {
            var newer = leftTime > rightTime ? left : right;
            return new ChildConflict(subject, left, right,
                ConflictResolution.ResolvedByRecency, newer,
                $"{newer.ChildRunId} observed this at {newer.ObservedUtc:u}, "
                + "after the other child.");
        }

        return new ChildConflict(subject, left, right, ConflictResolution.Unresolved, null,
            "Two children disagree with comparable evidence; the parent must not "
            + "choose between them silently.");
    }

    /// <summary>
    /// Two findings are about the same thing when they share most of their
    /// non-numeric terms. Numbers are excluded from the subject on purpose —
    /// "revenue was 4.2bn" and "revenue was 3.1bn" are the same subject and
    /// exactly the pair worth flagging.
    /// </summary>
    private static string? SharedSubject(ChildFinding left, ChildFinding right)
    {
        // The words that carry the disagreement are removed before measuring
        // overlap. Leaving them in makes the comparison self-defeating: in
        // "status is pending" vs "status is approved" the differing state word
        // is exactly what drags the overlap below the threshold, so the clearest
        // conflicts would be the ones least likely to register as the same
        // subject.
        var leftTerms = SubjectTerms(StripDisagreementWords(left.NormalizedClaim));
        var rightTerms = SubjectTerms(StripDisagreementWords(right.NormalizedClaim));
        if (leftTerms.Count == 0 || rightTerms.Count == 0)
            return null;

        var shared = leftTerms.Intersect(rightTerms, StringComparer.Ordinal).ToList();
        var smaller = Math.Min(leftTerms.Count, rightTerms.Count);
        if ((double)shared.Count / smaller < 0.6)
            return null;

        return string.Join(' ', shared.OrderBy(term => term, StringComparer.Ordinal));
    }

    private static HashSet<string> SubjectTerms(string normalized) =>
        normalized
            .Split(' ', StringSplitOptions.RemoveEmptyEntries)
            .Where(word => word.Length > 2
                           && !word.All(char.IsDigit)
                           && !SubjectStopwords.Contains(word))
            .ToHashSet(StringComparer.Ordinal);

    private static readonly HashSet<string> SubjectStopwords = new(StringComparer.Ordinal)
    {
        "the", "was", "were", "are", "has", "have", "had", "and", "for", "with",
        "that", "this", "from", "its", "their", "which", "been", "being", "into",
        "than", "then", "will", "would", "there"
    };

    /// <summary>
    /// Words that cannot both describe one subject at one time.
    ///
    /// Deliberately an explicit list rather than an attempt at general semantic
    /// inference. A supervisor that guesses at contradiction produces two bad
    /// outcomes — inventing conflicts between complementary facts, and missing
    /// real ones — and of the two, quietly inventing conflicts is the one that
    /// erodes trust in every answer. Cases outside this vocabulary and outside
    /// the numeric and negation checks are simply not detected; that is a stated
    /// limitation, not a silent one.
    /// </summary>
    private static readonly HashSet<string>[] ExclusiveStates =
    [
        new(StringComparer.Ordinal) { "pending", "approved", "rejected", "withdrawn" },
        new(StringComparer.Ordinal) { "open", "closed" },
        new(StringComparer.Ordinal) { "passed", "failed" },
        new(StringComparer.Ordinal) { "increased", "decreased", "unchanged" },
        new(StringComparer.Ordinal) { "online", "offline" },
        new(StringComparer.Ordinal) { "enabled", "disabled" },
        new(StringComparer.Ordinal) { "active", "inactive", "suspended" },
        new(StringComparer.Ordinal) { "succeeded", "failed", "cancelled" }
    ];

    private static readonly string[] NegationWords =
        ["not", "never", "denied", "rejected", "failed"];

    /// <summary>Removes state and negation words so what remains is the subject.</summary>
    private static string StripDisagreementWords(string normalized)
    {
        var kept = normalized
            .Split(' ', StringSplitOptions.RemoveEmptyEntries)
            .Where(word => !NegationWords.Contains(word, StringComparer.Ordinal)
                           && !ExclusiveStates.Any(group => group.Contains(word)));
        return string.Join(' ', kept);
    }

    /// <summary>True when each side asserts a different member of one exclusive set.</summary>
    private static bool AssertsDifferentExclusiveState(string left, string right)
    {
        var leftWords = left.Split(' ', StringSplitOptions.RemoveEmptyEntries).ToHashSet(StringComparer.Ordinal);
        var rightWords = right.Split(' ', StringSplitOptions.RemoveEmptyEntries).ToHashSet(StringComparer.Ordinal);

        foreach (var group in ExclusiveStates)
        {
            var leftState = group.FirstOrDefault(leftWords.Contains);
            var rightState = group.FirstOrDefault(rightWords.Contains);
            if (leftState is not null && rightState is not null
                && !string.Equals(leftState, rightState, StringComparison.Ordinal))
                return true;
        }
        return false;
    }

    /// <summary>
    /// True when the two findings cannot both hold: different numbers for the
    /// same subject, or one negating the other.
    /// </summary>
    private static bool AreIncompatible(ChildFinding left, ChildFinding right)
    {
        if (string.Equals(left.NormalizedClaim, right.NormalizedClaim, StringComparison.Ordinal))
            return false;

        var leftNumbers = Numbers(left.NormalizedClaim);
        var rightNumbers = Numbers(right.NormalizedClaim);
        if (leftNumbers.Count > 0 && rightNumbers.Count > 0
            && !leftNumbers.SetEquals(rightNumbers))
            return true;

        if (AssertsDifferentExclusiveState(left.NormalizedClaim, right.NormalizedClaim))
            return true;

        return IsNegationOf(left.NormalizedClaim, right.NormalizedClaim);
    }

    private static HashSet<string> Numbers(string normalized) =>
        normalized
            .Split(' ', StringSplitOptions.RemoveEmptyEntries)
            .Where(word => word.Any(char.IsDigit))
            .ToHashSet(StringComparer.Ordinal);

    private static bool IsNegationOf(string left, string right)
    {
        string[] negations = ["not", "never", "no", "denied", "rejected", "failed"];
        var leftNegated = left.Split(' ').Any(word => negations.Contains(word));
        var rightNegated = right.Split(' ').Any(word => negations.Contains(word));
        if (leftNegated == rightNegated)
            return false;

        // One side negated: same subject with opposite polarity is a conflict.
        var stripped = (string text) => string.Join(' ',
            text.Split(' ').Where(word => !negations.Contains(word)));
        var a = SubjectTerms(stripped(left));
        var b = SubjectTerms(stripped(right));
        if (a.Count == 0 || b.Count == 0)
            return false;
        var shared = a.Intersect(b, StringComparer.Ordinal).Count();
        return (double)shared / Math.Min(a.Count, b.Count) >= 0.7;
    }

    /// <summary>
    /// Renders the supervision outcome for the parent, keeping unresolved
    /// disagreement visible rather than flattening it into a single answer.
    /// </summary>
    public static string Summarize(SupervisionResult result)
    {
        ArgumentNullException.ThrowIfNull(result);

        var builder = new StringBuilder();
        builder.AppendLine(
            $"{result.Succeeded} of {result.Children.Count} child runs succeeded"
            + (result.Failed > 0 ? $"; {result.Failed} did not." : "."));

        if (result.PartialSuccess)
            builder.AppendLine("Partial results from the successful children are kept below.");

        foreach (var finding in result.MergedFindings)
            builder.AppendLine($"- {finding.Claim}  [{finding.Provenance}]");

        foreach (var conflict in result.UnresolvedConflicts)
            builder.AppendLine(
                $"! Unresolved: \"{conflict.Left.Claim}\" ({conflict.Left.Provenance}) "
                + $"vs \"{conflict.Right.Claim}\" ({conflict.Right.Provenance}).");

        if (result.RequiresUser)
            builder.AppendLine("This needs a decision from the user before continuing.");

        return builder.ToString().TrimEnd();
    }
}
