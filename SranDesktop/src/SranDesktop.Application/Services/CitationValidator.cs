namespace SranDesktop.Application.Services;

using SranDesktop.Application.Models;

/// <summary>
/// The last gate before an answer is allowed out.
///
/// Its job is narrow and unglamorous: check that every citation points at a
/// source that exists, and that the source actually says the thing it is cited
/// for. The failure this prevents is citation laundering — attaching a real,
/// reputable URL to a sentence that URL never supported. That reads as more
/// trustworthy than an uncited claim while being strictly worse, because the
/// citation invites the reader to stop checking.
/// </summary>
public static class CitationValidator
{
    /// <summary>
    /// Validates a finished answer's claim graph.
    /// </summary>
    /// <param name="requireIndependentConfirmation">
    /// When true, a claim supported only by copies of one source is rejected.
    /// </param>
    /// <param name="maximumEvidenceAge">
    /// How old the newest supporting source may be for a date-sensitive claim.
    /// </param>
    /// <param name="requireOutrightSupport">
    /// When true, a claim whose sources only partially state it is rejected.
    ///
    /// This is the difference between a chat answer and a generated document. In
    /// conversation a partially-supported statement can carry its hedge — "one
    /// source suggests" — and the reader sees the uncertainty. Rendering it into
    /// a report drops the hedge and keeps the sentence, so anything short of the
    /// source stating the claim outright must not reach one.
    /// </param>
    public static CitationValidationResult Validate(
        ClaimSourceGraph graph,
        DateTimeOffset now,
        bool requireIndependentConfirmation = false,
        TimeSpan? maximumEvidenceAge = null,
        bool requireOutrightSupport = false)
    {
        ArgumentNullException.ThrowIfNull(graph);

        var issues = new List<CitationIssue>();
        var valid = 0;
        var sourcesById = graph.Sources.ToDictionary(
            s => s.Source.SourceId, StringComparer.Ordinal);

        foreach (var claim in graph.Claims)
        {
            var claimIssues = new List<CitationIssue>();

            foreach (var evidence in claim.Supporting)
            {
                // 1. The cited source has to be in the run's evidence at all.
                if (!sourcesById.TryGetValue(evidence.SourceId, out var ranked))
                {
                    claimIssues.Add(new CitationIssue(
                        claim.ClaimId, evidence.SourceId, CitationVerdict.SourceMissing,
                        "The citation names a source that is not in this run's evidence."));
                    continue;
                }

                // 2. A page that tried to issue instructions cannot be evidence.
                if (ranked.Source.IsQuarantined)
                {
                    claimIssues.Add(new CitationIssue(
                        claim.ClaimId, evidence.SourceId, CitationVerdict.QuarantinedSource,
                        "The cited page contains instruction-shaped text and was quarantined."));
                    continue;
                }

                // 3. The span has to be real and still present in the source.
                if (!SpanIsPresent(ranked.Source.Content, evidence))
                {
                    claimIssues.Add(new CitationIssue(
                        claim.ClaimId, evidence.SourceId, CitationVerdict.SourceDoesNotSupportClaim,
                        "The quoted evidence span is not present in the source text."));
                    continue;
                }

                // 4. Topical proximity is not support.
                if (evidence.Strength < 0.4)
                    claimIssues.Add(new CitationIssue(
                        claim.ClaimId, evidence.SourceId, CitationVerdict.IrrelevantPage,
                        $"The cited page is only loosely related (overlap {evidence.Strength:P0})."));
            }

            // 5. Stated only partially, where the destination cannot carry a hedge.
            if (requireOutrightSupport
                && claim.Supporting.Count > 0
                && claim.Status != ClaimSupport.Supported)
                claimIssues.Add(new CitationIssue(
                    claim.ClaimId, null, CitationVerdict.SourceDoesNotSupportClaim,
                    $"No source states this outright (status {claim.Status}); "
                    + "it must not be rendered as fact."));

            // 6. Nothing cited at all, for a claim that asserts something.
            if (claim.Supporting.Count == 0)
                claimIssues.Add(new CitationIssue(
                    claim.ClaimId, null, CitationVerdict.SourceDoesNotSupportClaim,
                    "No source supports this claim."));

            // 6. Ten copies of one report are still one report.
            if (requireIndependentConfirmation
                && claim.Supporting.Count > 0
                && claim.IndependentSupportCount < 2)
                claimIssues.Add(new CitationIssue(
                    claim.ClaimId, null, CitationVerdict.DuplicateOnlySupport,
                    "Supported only by one underlying source; copies do not confirm it."));

            // 7. A current-state claim backed by stale evidence.
            if (claim.IsDateSensitive && maximumEvidenceAge is { } maximumAge)
            {
                var newest = claim.Supporting
                    .Select(e => sourcesById.GetValueOrDefault(e.SourceId))
                    .Where(s => s is not null)
                    .Select(s => s!.Source.Dates.Best)
                    .Where(d => d is not null)
                    .DefaultIfEmpty(null)
                    .Max();

                if (newest is null || now - newest.Value > maximumAge)
                    claimIssues.Add(new CitationIssue(
                        claim.ClaimId, null, CitationVerdict.OutdatedEvidence,
                        newest is null
                            ? "Time-sensitive claim with no dated evidence."
                            : $"Newest evidence is from {newest.Value:yyyy-MM-dd}."));
            }

            if (claimIssues.Count == 0)
                valid++;
            else
                issues.AddRange(claimIssues);
        }

        return new CitationValidationResult(
            issues.Count == 0, issues, graph.Claims.Count, valid);
    }

    /// <summary>
    /// Confirms the quoted span is really in the source.
    ///
    /// Checked by content rather than by offset: offsets go stale the moment a
    /// page is re-fetched and the boilerplate shifts by a character, and a stale
    /// offset would silently start "verifying" the wrong sentence.
    /// </summary>
    private static bool SpanIsPresent(string content, ClaimEvidence evidence)
    {
        if (string.IsNullOrWhiteSpace(evidence.EvidenceSpan))
            return false;
        return content.Contains(evidence.EvidenceSpan.Trim(), StringComparison.Ordinal);
    }

    /// <summary>
    /// Scans a draft answer for factual-looking sentences that carry no claim in
    /// the graph — the unsupported-claim sweep that runs after composition.
    /// </summary>
    public static IReadOnlyList<string> FindUncitedAssertions(
        string answer, ClaimSourceGraph graph)
    {
        ArgumentNullException.ThrowIfNull(graph);
        if (string.IsNullOrWhiteSpace(answer))
            return [];

        var known = graph.Claims
            .Select(claim => claim.NormalizedText)
            .ToList();

        var uncited = new List<string>();
        foreach (var sentence in answer.Split(['.', '\n'], StringSplitOptions.RemoveEmptyEntries))
        {
            var trimmed = sentence.Trim();
            if (trimmed.Length < 20 || !LooksFactual(trimmed))
                continue;

            var normalized = new string(trimmed
                .Where(c => char.IsLetterOrDigit(c) || char.IsWhiteSpace(c))
                .ToArray())
                .ToLowerInvariant()
                .Trim();

            if (!known.Any(claim =>
                    normalized.Contains(claim, StringComparison.Ordinal)
                    || claim.Contains(normalized, StringComparison.Ordinal)))
                uncited.Add(trimmed);
        }
        return uncited;
    }

    /// <summary>
    /// Distinguishes an assertion about the world from hedging or narration.
    /// Hedged text is not treated as a factual claim, which is the point of
    /// hedging; numbers and dates are strong signals that it is.
    /// </summary>
    private static bool LooksFactual(string sentence)
    {
        var lower = sentence.ToLowerInvariant();
        string[] hedges =
        [
            "might", "may ", "could", "perhaps", "possibly", "unclear",
            "i think", "it seems", "appears to", "reportedly", "allegedly"
        ];
        if (hedges.Any(hedge => lower.Contains(hedge, StringComparison.Ordinal)))
            return false;

        return sentence.Any(char.IsDigit)
               || lower.Contains(" is ", StringComparison.Ordinal)
               || lower.Contains(" was ", StringComparison.Ordinal)
               || lower.Contains(" has ", StringComparison.Ordinal)
               || lower.Contains(" will ", StringComparison.Ordinal);
    }
}
