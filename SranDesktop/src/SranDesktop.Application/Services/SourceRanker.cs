using System.Security.Cryptography;
using System.Text;

namespace SranDesktop.Application.Services;

using SranDesktop.Application.Models;

/// <summary>
/// Orders sources and, more importantly, works out which of them are actually
/// separate pieces of evidence.
///
/// The ranking is the easy half. The half that decides whether an answer is
/// honest is deduplication: three pages carrying the same wire story look like
/// three confirmations and are one. Counting them as three is how a research
/// tool reports a single unverified report as corroborated fact, so every
/// source here is classified against the ones already accepted, and only
/// <see cref="SourceRelation.Independent"/> is allowed to add confirmation.
/// </summary>
public static class SourceRanker
{
    /// <summary>Jaccard similarity over shingles above which two bodies are the
    /// same text. Deliberately strict — near-duplicates are common and real
    /// independent reporting on one event does share a lot of vocabulary.</summary>
    private const double MirrorSimilarity = 0.90;

    /// <summary>Fraction of the shorter document's phrasing that must appear in
    /// the longer one before it is treated as a copy with added chrome.</summary>
    private const double ContainmentSimilarity = 0.90;

    public static string ComputeContentHash(string content)
    {
        var normalized = NormalizeForComparison(content);
        return Convert.ToHexString(
            SHA256.HashData(Encoding.UTF8.GetBytes(normalized)));
    }

    /// <summary>
    /// Ranks sources and marks duplicates. Order is stable for a given input so
    /// the same evidence always produces the same citation numbering.
    /// </summary>
    public static IReadOnlyList<RankedSource> Rank(
        IEnumerable<ResearchSource> sources,
        DateTimeOffset now,
        DateTimeOffset? freshnessFloor = null)
    {
        ArgumentNullException.ThrowIfNull(sources);

        var scored = sources
            .Select(source => (Source: source, Score: Score(source, now, freshnessFloor)))
            .OrderByDescending(item => item.Score)
            .ThenBy(item => item.Source.SourceId, StringComparer.Ordinal)
            .ToList();

        var accepted = new List<ResearchSource>();
        var ranked = new List<RankedSource>(scored.Count);

        foreach (var (source, score) in scored)
        {
            var (relation, duplicateOf, reasons) = Classify(source, accepted);
            if (relation == SourceRelation.Independent)
                accepted.Add(source);

            ranked.Add(new RankedSource(
                source,
                score,
                ranked.Count + 1,
                relation,
                duplicateOf,
                [.. reasons, .. ScoreReasons(source, now, freshnessFloor)]));
        }

        return ranked;
    }

    /// <summary>
    /// Decides whether <paramref name="candidate"/> is new evidence relative to
    /// what has already been accepted. The checks run cheapest-first, and the
    /// first relationship found wins.
    /// </summary>
    private static (SourceRelation Relation, string? DuplicateOf, List<string> Reasons)
        Classify(ResearchSource candidate, List<ResearchSource> accepted)
    {
        foreach (var existing in accepted)
        {
            // 1. Literally the same document, possibly reached by two URLs.
            if (SameDocument(candidate, existing))
                return (SourceRelation.SameDocument, existing.SourceId,
                    ["Same document as an already-counted source."]);

            // 2. Same body text served from somewhere else.
            if (string.Equals(candidate.ContentHash, existing.ContentHash, StringComparison.Ordinal))
                return (SourceRelation.Mirror, existing.SourceId,
                    ["Byte-identical body to an already-counted source."]);

            if (BodySimilarity(candidate.Content, existing.Content) >= MirrorSimilarity
                || IsContainedCopy(candidate.Content, existing.Content))
                return (SourceRelation.Mirror, existing.SourceId,
                    ["Body text is a near-copy of an already-counted source."]);

            // 3. Both credit the same originating newsroom.
            if (!string.IsNullOrWhiteSpace(candidate.AttributedTo)
                && string.Equals(candidate.AttributedTo, existing.AttributedTo,
                    StringComparison.OrdinalIgnoreCase))
                return (SourceRelation.Syndicated, existing.SourceId,
                    [$"Both credit {candidate.AttributedTo}; one underlying report."]);

            // 4. This one credits a publisher that is already counted directly.
            if (!string.IsNullOrWhiteSpace(candidate.AttributedTo)
                && (string.Equals(candidate.AttributedTo, existing.Identity.Publisher,
                        StringComparison.OrdinalIgnoreCase)
                    || string.Equals(candidate.AttributedTo, existing.Identity.SiteKey,
                        StringComparison.OrdinalIgnoreCase)))
                return (SourceRelation.SharedUpstreamSource, existing.SourceId,
                    ["Reports the already-counted source's reporting."]);

            // 5. The accepted one credits THIS one — the copy arrived first.
            if (!string.IsNullOrWhiteSpace(existing.AttributedTo)
                && (string.Equals(existing.AttributedTo, candidate.Identity.Publisher,
                        StringComparison.OrdinalIgnoreCase)
                    || string.Equals(existing.AttributedTo, candidate.Identity.SiteKey,
                        StringComparison.OrdinalIgnoreCase)))
                return (SourceRelation.SharedUpstreamSource, existing.SourceId,
                    ["An already-counted source credits this one; same reporting."]);
        }

        return (SourceRelation.Independent, null, []);
    }

    private static bool SameDocument(ResearchSource a, ResearchSource b)
    {
        if (string.Equals(NormalizeUrl(a.Url), NormalizeUrl(b.Url), StringComparison.OrdinalIgnoreCase))
            return true;

        var canonicalA = a.Identity.CanonicalUrl ?? a.Url;
        var canonicalB = b.Identity.CanonicalUrl ?? b.Url;
        return string.Equals(
            NormalizeUrl(canonicalA), NormalizeUrl(canonicalB), StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// Strips the parts of a URL that do not change which document is served —
    /// scheme, "www.", tracking parameters, fragment, trailing slash. Without
    /// this, one page reached from a search result and from a share link looks
    /// like two independent confirmations.
    /// </summary>
    public static string NormalizeUrl(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri))
            return url.Trim().TrimEnd('/');

        var host = uri.Host.StartsWith("www.", StringComparison.OrdinalIgnoreCase)
            ? uri.Host[4..]
            : uri.Host;

        var keptQuery = uri.Query
            .TrimStart('?')
            .Split('&', StringSplitOptions.RemoveEmptyEntries)
            .Where(pair => !IsTrackingParameter(pair))
            .OrderBy(pair => pair, StringComparer.Ordinal)
            .ToArray();

        var path = uri.AbsolutePath.TrimEnd('/');
        return keptQuery.Length == 0
            ? $"{host}{path}"
            : $"{host}{path}?{string.Join('&', keptQuery)}";
    }

    private static bool IsTrackingParameter(string pair)
    {
        var name = pair.Split('=', 2)[0];
        return name.StartsWith("utm_", StringComparison.OrdinalIgnoreCase)
               || name is "fbclid" or "gclid" or "mc_cid" or "mc_eid" or "ref" or "ref_src";
    }

    /// <summary>
    /// Jaccard similarity over 5-word shingles. Shingles rather than bag-of-words
    /// because word overlap alone is high for any two articles on one topic;
    /// shared phrasing is what actually indicates a copy.
    /// </summary>
    public static double BodySimilarity(string left, string right)
    {
        if (string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right))
            return 0;

        var a = Shingles(left);
        var b = Shingles(right);
        if (a.Count == 0 || b.Count == 0)
            return 0;

        var intersection = a.Intersect(b, StringComparer.Ordinal).Count();
        var union = a.Count + b.Count - intersection;
        return union == 0 ? 0 : (double)intersection / union;
    }

    /// <summary>
    /// True when one body is essentially the other plus extra material.
    ///
    /// Jaccard alone misses the commonest real mirror: the same article wrapped
    /// in a different site's navigation, related-links and "Read more" chrome.
    /// The added text drives the union up and the score down, so a verbatim copy
    /// scores as merely similar and gets counted as independent confirmation.
    /// Containment asks the question that actually matters — is nearly all of
    /// one document present inside the other — and stays quiet for genuinely
    /// independent reporting, which differs throughout rather than only at the
    /// edges.
    /// </summary>
    private static bool IsContainedCopy(string left, string right)
    {
        var a = Shingles(left);
        var b = Shingles(right);

        // Very short texts share phrasing by chance; containment is meaningless there.
        if (a.Count < 4 || b.Count < 4)
            return false;

        var intersection = a.Intersect(b, StringComparer.Ordinal).Count();
        var smaller = Math.Min(a.Count, b.Count);
        return (double)intersection / smaller >= ContainmentSimilarity;
    }

    private static HashSet<string> Shingles(string text)
    {
        var words = NormalizeForComparison(text)
            .Split(' ', StringSplitOptions.RemoveEmptyEntries);
        var shingles = new HashSet<string>(StringComparer.Ordinal);
        for (var index = 0; index + 5 <= words.Length; index++)
            shingles.Add(string.Join(' ', words.AsSpan(index, 5).ToArray()));

        // Text shorter than one shingle still has to compare to something.
        if (shingles.Count == 0 && words.Length > 0)
            shingles.Add(string.Join(' ', words));
        return shingles;
    }

    private static string NormalizeForComparison(string text)
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

    private static double Score(
        ResearchSource source, DateTimeOffset now, DateTimeOffset? freshnessFloor)
    {
        // A quarantined source is ranked last rather than dropped: the run still
        // needs to be able to say what it found and why it refused to use it.
        if (source.IsQuarantined)
            return -1;

        var score = 0.0;
        score += source.AuthorityScore * 0.35;
        score += source.IsPrimary ? 0.25 : 0;
        score += Freshness(source, now) * 0.20;
        score += source.Medium == SourceMedium.StructuredData ? 0.05 : 0;
        score += string.IsNullOrWhiteSpace(source.AttributedTo) ? 0.10 : 0;
        score += source.Dates.Best is not null ? 0.05 : 0;

        // A source older than the floor is not disqualified — it may still be
        // the primary record — but it cannot outrank something in range.
        if (freshnessFloor is not null
            && source.Dates.Best is not null
            && source.Dates.Best < freshnessFloor)
            score -= 0.30;

        return Math.Round(score, 6);
    }

    private static double Freshness(ResearchSource source, DateTimeOffset now)
    {
        if (source.Dates.Best is not { } date)
            return 0.3;
        var age = now - date;
        if (age < TimeSpan.Zero)
            return 0.5;
        return age.TotalDays switch
        {
            <= 7 => 1.0,
            <= 30 => 0.85,
            <= 180 => 0.6,
            <= 365 => 0.4,
            <= 365 * 3 => 0.2,
            _ => 0.05
        };
    }

    private static IEnumerable<string> ScoreReasons(
        ResearchSource source, DateTimeOffset now, DateTimeOffset? freshnessFloor)
    {
        if (source.IsQuarantined)
            yield return "Quarantined: the page contains instruction-shaped text.";
        if (source.IsPrimary)
            yield return "Primary source.";
        if (!string.IsNullOrWhiteSpace(source.AttributedTo))
            yield return $"Credits {source.AttributedTo}.";
        if (freshnessFloor is not null && source.Dates.Best is { } best && best < freshnessFloor)
            yield return "Older than the question's freshness requirement.";
        if (source.Dates.Best is null)
            yield return "No publication date found.";
    }
}
