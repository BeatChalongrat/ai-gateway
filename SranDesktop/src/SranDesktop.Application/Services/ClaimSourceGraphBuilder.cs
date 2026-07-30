using System.Text;

namespace SranDesktop.Application.Services;

using SranDesktop.Application.Models;

/// <summary>
/// Ties each factual claim to the evidence for and against it.
///
/// The rule the whole thing exists to enforce: a claim's support comes from
/// finding the claim's own terms in a source's text, not from the source having
/// been fetched during the same research run. Presence in the result set is not
/// evidence, and treating it as evidence is exactly how a confident answer ends
/// up citing a page that never said the thing.
/// </summary>
public static class ClaimSourceGraphBuilder
{
    /// <summary>Fraction of a claim's content words that must appear in a span
    /// before that span counts as stating it outright.</summary>
    private const double SupportThreshold = 0.8;

    /// <summary>
    /// Below this, an overlap is topical coincidence and not evidence at all.
    ///
    /// Set high on purpose. At a lower bar, "the review of the merger remains
    /// open" registers as partial support for "the merger was approved" — it
    /// shares the subject and misses the entire assertion. A sentence that
    /// matches half a claim's terms while omitting the term the claim is about
    /// is not weak evidence for it; frequently it is evidence against it.
    /// </summary>
    private const double PartialThreshold = 0.6;

    public static ClaimSourceGraph Build(
        IEnumerable<string> claimTexts,
        IReadOnlyList<RankedSource> sources,
        DateTimeOffset now,
        TimeSpan? dateSensitiveWindow = null)
    {
        ArgumentNullException.ThrowIfNull(claimTexts);
        ArgumentNullException.ThrowIfNull(sources);

        var window = dateSensitiveWindow ?? TimeSpan.FromDays(365);
        var claims = new List<ResearchClaim>();
        var index = 0;

        foreach (var text in claimTexts)
        {
            if (string.IsNullOrWhiteSpace(text))
                continue;

            var normalized = Normalize(text);
            var terms = ContentTerms(normalized);
            var supporting = new List<ClaimEvidence>();
            var contradicting = new List<ClaimEvidence>();

            foreach (var ranked in sources)
            {
                // A quarantined page may not support anything. Text that tried to
                // issue instructions is not evidence, whatever else it says.
                if (ranked.Source.IsQuarantined)
                    continue;

                var match = FindBestSpan(ranked.Source.Content, terms);
                if (match is null)
                    continue;

                if (match.Value.Negated)
                    contradicting.Add(new ClaimEvidence(
                        ranked.Source.SourceId, match.Value.Span,
                        match.Value.Start, match.Value.Length, match.Value.Strength));
                else if (match.Value.Strength >= PartialThreshold)
                    supporting.Add(new ClaimEvidence(
                        ranked.Source.SourceId, match.Value.Span,
                        match.Value.Start, match.Value.Length, match.Value.Strength));
            }

            var isDateSensitive = LooksDateSensitive(normalized);
            var independent = CountIndependent(supporting, sources);
            var (status, reason, confidence) = Classify(
                supporting, contradicting, independent, isDateSensitive,
                sources, now, window);

            claims.Add(new ResearchClaim
            {
                ClaimId = $"claim-{++index:D3}",
                Text = text.Trim(),
                NormalizedText = normalized,
                Supporting = supporting,
                Contradicting = contradicting,
                IsDateSensitive = isDateSensitive,
                Status = status,
                StatusReason = reason,
                Confidence = confidence,
                IndependentSupportCount = independent
            });
        }

        return new ClaimSourceGraph(claims, sources);
    }

    /// <summary>
    /// Copies do not add confirmation. Support is counted per independent
    /// source, and a duplicate's backing collapses onto whatever it duplicates.
    /// </summary>
    private static int CountIndependent(
        List<ClaimEvidence> supporting, IReadOnlyList<RankedSource> sources)
    {
        var roots = new HashSet<string>(StringComparer.Ordinal);
        foreach (var evidence in supporting)
        {
            var ranked = sources.FirstOrDefault(s => s.Source.SourceId == evidence.SourceId);
            if (ranked is null)
                continue;
            roots.Add(ranked.CountsAsIndependent
                ? ranked.Source.SourceId
                : ranked.DuplicateOfSourceId ?? ranked.Source.SourceId);
        }
        return roots.Count;
    }

    private static (ClaimSupport Status, string Reason, double Confidence) Classify(
        List<ClaimEvidence> supporting,
        List<ClaimEvidence> contradicting,
        int independent,
        bool isDateSensitive,
        IReadOnlyList<RankedSource> sources,
        DateTimeOffset now,
        TimeSpan window)
    {
        if (supporting.Count == 0 && contradicting.Count == 0)
            return (ClaimSupport.Unsupported, "No source contains this claim.", 0);

        if (supporting.Count == 0)
            return (ClaimSupport.Unsupported,
                "Every source that mentions this contradicts it.", 0);

        // Disagreement between real sources is reported, never resolved by
        // picking the more convenient one.
        if (contradicting.Count > 0)
        {
            var confidence = Math.Min(0.5,
                supporting.Max(e => e.Strength) - contradicting.Max(e => e.Strength) + 0.3);
            return (ClaimSupport.Conflicted,
                $"{supporting.Count} source(s) support and {contradicting.Count} contradict this.",
                Math.Max(0, Math.Round(confidence, 3)));
        }

        if (isDateSensitive)
        {
            var freshest = supporting
                .Select(e => sources.FirstOrDefault(s => s.Source.SourceId == e.SourceId))
                .Where(s => s is not null)
                .Select(s => s!.Source.Dates.Best)
                .Where(d => d is not null)
                .DefaultIfEmpty(null)
                .Max();

            if (freshest is null)
                return (ClaimSupport.PartiallySupported,
                    "Time-sensitive, but no source carries a date.", 0.4);

            if (now - freshest.Value > window)
                return (ClaimSupport.Outdated,
                    $"Newest supporting source is from {freshest.Value:yyyy-MM-dd}; "
                    + "the claim depends on current information.",
                    0.3);
        }

        var best = supporting.Max(e => e.Strength);
        if (best < SupportThreshold || independent < 1)
            return (ClaimSupport.PartiallySupported,
                "Sources are related to the claim but do not state it outright.",
                Math.Round(best * 0.7, 3));

        // A second independent source raises confidence; more copies do not.
        var scored = Math.Min(0.99, best * (independent >= 2 ? 1.0 : 0.85));
        return (ClaimSupport.Supported,
            independent >= 2
                ? $"Stated by {independent} independent sources."
                : "Stated by one source; no independent confirmation found.",
            Math.Round(scored, 3));
    }

    /// <summary>
    /// Finds the sentence that best matches the claim's content terms, and says
    /// whether that sentence asserts or denies it.
    /// </summary>
    private static (string Span, int Start, int Length, double Strength, bool Negated)?
        FindBestSpan(string content, IReadOnlyCollection<string> terms)
    {
        if (string.IsNullOrWhiteSpace(content) || terms.Count == 0)
            return null;

        (string Span, int Start, int Length, double Strength, bool Negated)? best = null;

        foreach (var (start, length) in Sentences(content))
        {
            var sentence = content.Substring(start, length);
            var sentenceTerms = ContentTerms(Normalize(sentence));
            if (sentenceTerms.Count == 0)
                continue;

            var hits = terms.Count(term => sentenceTerms.Contains(term));
            var strength = (double)hits / terms.Count;
            if (strength <= 0)
                continue;

            if (best is null || strength > best.Value.Strength)
                best = (sentence.Trim(), start, length,
                    Math.Round(strength, 3), IsNegated(sentence));
        }

        return best is { Strength: > 0 } ? best : null;
    }

    /// <summary>
    /// Splits content into sentences.
    ///
    /// The decimal case is the one that matters. Treating every '.' as a
    /// terminator cuts "requires TLS 1.3" into "requires TLS 1." — and that
    /// truncated string is what would be stored as the evidence span and shown
    /// to the user as the quote proving the claim. A citation that quotes a
    /// mangled number is worse than no citation, because it looks checked.
    /// </summary>
    private static IEnumerable<(int Start, int Length)> Sentences(string content)
    {
        var start = 0;
        for (var index = 0; index < content.Length; index++)
        {
            var character = content[index];
            if (character is not ('.' or '!' or '?' or '\n'))
                continue;

            // 1.3, 4.2 billion, v2.0 — a period between digits is part of a number.
            if (character == '.'
                && index > 0 && index + 1 < content.Length
                && char.IsDigit(content[index - 1])
                && char.IsDigit(content[index + 1]))
                continue;

            // "approx. 40" / "U.S. filing" — a period not followed by whitespace
            // is inside a token rather than ending a sentence.
            if (character == '.'
                && index + 1 < content.Length
                && !char.IsWhiteSpace(content[index + 1]))
                continue;

            var length = index - start + 1;
            if (length > 1)
                yield return (start, length);
            start = index + 1;
        }
        if (start < content.Length)
            yield return (start, content.Length - start);
    }

    /// <summary>
    /// Cheap negation detection. It is deliberately conservative: a false
    /// "negated" reading downgrades a claim to Conflicted, which is a safe
    /// direction to be wrong in, while missing a negation would let a denial
    /// count as support.
    /// </summary>
    private static bool IsNegated(string sentence)
    {
        var normalized = Normalize(sentence);
        string[] markers =
        [
            " not ", " no ", " never ", " denied ", " denies ", " rejected ",
            " false ", " incorrect ", " untrue ", " refuted ", " disputes ",
            " disputed ", " failed to ", " did not ", " does not ", " was not ",
            " were not ", " is not ", " are not ", " cannot "
        ];
        return markers.Any(marker => $" {normalized} ".Contains(marker, StringComparison.Ordinal));
    }

    private static bool LooksDateSensitive(string normalized)
    {
        string[] markers =
        [
            "current", "currently", "now", "today", "latest", "as of", "this year",
            "recent", "recently", "at present", "up to date", "still"
        ];
        return markers.Any(marker =>
            normalized.Contains(marker, StringComparison.Ordinal));
    }

    /// <summary>Words that carry the claim. Stopwords are dropped so "the" and
    /// "is" cannot inflate a match to the point of looking like support.</summary>
    private static HashSet<string> ContentTerms(string normalized)
    {
        var terms = new HashSet<string>(StringComparer.Ordinal);
        foreach (var word in normalized.Split(' ', StringSplitOptions.RemoveEmptyEntries))
        {
            if (word.Length <= 2 || Stopwords.Contains(word))
                continue;
            terms.Add(word);
        }
        return terms;
    }

    private static readonly HashSet<string> Stopwords = new(StringComparer.Ordinal)
    {
        "the", "and", "for", "with", "that", "this", "from", "was", "were", "are",
        "has", "have", "had", "but", "not", "its", "it's", "their", "there", "which",
        "will", "would", "been", "being", "into", "than", "then", "they", "them",
        "when", "what", "who", "how", "why", "can", "could", "should", "about",
        "after", "before", "over", "under", "between", "also", "more", "most",
        "some", "such", "only", "other", "these", "those", "each", "per"
    };

    private static string Normalize(string text)
    {
        var builder = new StringBuilder(text.Length);
        var lastWasSpace = false;
        foreach (var character in text)
        {
            if (char.IsLetterOrDigit(character))
            {
                builder.Append(char.ToLowerInvariant(character));
                lastWasSpace = false;
            }
            else if (!lastWasSpace)
            {
                builder.Append(' ');
                lastWasSpace = true;
            }
        }
        return builder.ToString().Trim();
    }
}
