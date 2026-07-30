namespace SranDesktop.Application.Models;

/// <summary>
/// How a source was rendered before its text was extracted. This matters for
/// trust, not just plumbing: a JavaScript-rendered page went through a browser
/// that executed the page's own code, and a PDF went through a parser that has
/// historically been a good place to hide text a reader never sees.
/// </summary>
public enum SourceMedium
{
    Html,
    Pdf,
    JavaScriptRendered,
    StructuredData,
    Unknown
}

/// <summary>
/// Why one source is not independent evidence from another.
/// </summary>
public enum SourceRelation
{
    /// <summary>No relationship found; counts as independent.</summary>
    Independent,

    /// <summary>The same URL, or the same canonical URL, seen twice.</summary>
    SameDocument,

    /// <summary>Byte-identical or near-identical body text on a different host.</summary>
    Mirror,

    /// <summary>A wire/newsroom copy that names the same originating publisher.</summary>
    Syndicated,

    /// <summary>Different text, but both attribute the claim to the same upstream source.</summary>
    SharedUpstreamSource
}

/// <summary>Who published something, as far as it can be established.</summary>
public sealed record SourceIdentity(
    string Domain,
    string? Publisher = null,
    string? Author = null,
    string? CanonicalUrl = null)
{
    /// <summary>
    /// Registrable-ish domain, used to tell "two pages on one site" from "two
    /// sites". Deliberately simple: the last two labels, which is right for
    /// example.com and wrong for co.uk — so the ranker treats it as a hint and
    /// never as proof of independence on its own.
    /// </summary>
    public string SiteKey
    {
        get
        {
            var labels = Domain.Split('.', StringSplitOptions.RemoveEmptyEntries);
            return labels.Length <= 2
                ? Domain.ToLowerInvariant()
                : string.Join('.', labels[^2..]).ToLowerInvariant();
        }
    }
}

/// <summary>
/// The dates a page carries. Kept apart on purpose: "published" and "the event
/// happened" answer different questions, and a page updated last week about an
/// event from 2019 is fresh and stale at the same time depending on which one
/// the claim depends on.
/// </summary>
public sealed record SourceDates(
    DateTimeOffset? Published = null,
    DateTimeOffset? Updated = null,
    DateTimeOffset? EventDate = null)
{
    public DateTimeOffset? Best => Updated ?? Published ?? EventDate;
}

/// <summary>A fetched, parsed source with everything needed to cite it.</summary>
public sealed record ResearchSource
{
    public required string SourceId { get; init; }
    public required string Url { get; init; }
    public required string Title { get; init; }
    public required SourceIdentity Identity { get; init; }
    public SourceMedium Medium { get; init; } = SourceMedium.Html;
    public SourceDates Dates { get; init; } = new();

    /// <summary>Extracted body text. Never shown to the model unwrapped.</summary>
    public string Content { get; init; } = "";

    /// <summary>SHA-256 of normalized content — the dedup primitive.</summary>
    public required string ContentHash { get; init; }

    /// <summary>True when the source carries the evidence rather than describing
    /// someone else's. A regulator's own filing, not an article about it.</summary>
    public bool IsPrimary { get; init; }

    /// <summary>Publisher this source credits the underlying reporting to, when
    /// it says so. Two articles crediting the same wire are one source.</summary>
    public string? AttributedTo { get; init; }

    /// <summary>0..1 editorial-authority prior supplied by the caller's policy.</summary>
    public double AuthorityScore { get; init; } = 0.5;

    /// <summary>Set when the source tripped the injection scanner.</summary>
    public IReadOnlyList<string> InjectionFindings { get; init; } = [];

    public bool IsQuarantined => InjectionFindings.Count > 0;
}

/// <summary>Where a source ended up after ranking, and why.</summary>
public sealed record RankedSource(
    ResearchSource Source,
    double Score,
    int Rank,
    SourceRelation Relation,
    string? DuplicateOfSourceId,
    IReadOnlyList<string> Reasons)
{
    /// <summary>Only independent sources may be counted as confirmation.</summary>
    public bool CountsAsIndependent => Relation == SourceRelation.Independent;
}

public enum ClaimSupport
{
    Supported,
    PartiallySupported,
    Conflicted,
    Unsupported,
    Outdated
}

/// <summary>A single factual assertion, tied to the evidence for and against it.</summary>
public sealed record ResearchClaim
{
    public required string ClaimId { get; init; }

    /// <summary>The claim as written into the answer.</summary>
    public required string Text { get; init; }

    /// <summary>Lowercased, punctuation-stripped form used for matching.</summary>
    public required string NormalizedText { get; init; }

    public IReadOnlyList<ClaimEvidence> Supporting { get; init; } = [];
    public IReadOnlyList<ClaimEvidence> Contradicting { get; init; } = [];

    /// <summary>True when the claim's truth depends on how recent the evidence is.</summary>
    public bool IsDateSensitive { get; init; }

    /// <summary>Set when the claim is about a specific moment.</summary>
    public DateTimeOffset? AsOf { get; init; }

    public ClaimSupport Status { get; init; } = ClaimSupport.Unsupported;
    public double Confidence { get; init; }
    public string? StatusReason { get; init; }

    /// <summary>Independent supporting sources — copies of one source count once.</summary>
    public int IndependentSupportCount { get; init; }
}

/// <summary>One source's evidence for or against a claim, with the span that shows it.</summary>
public sealed record ClaimEvidence(
    string SourceId,
    string EvidenceSpan,
    int SpanStart,
    int SpanLength,
    double Strength)
{
    public bool HasSpan => SpanLength > 0;
}

/// <summary>The claim/source graph behind an answer.</summary>
public sealed record ClaimSourceGraph(
    IReadOnlyList<ResearchClaim> Claims,
    IReadOnlyList<RankedSource> Sources)
{
    public IReadOnlyList<ResearchClaim> Unsupported =>
        Claims.Where(c => c.Status == ClaimSupport.Unsupported).ToList();

    public IReadOnlyList<ResearchClaim> Conflicted =>
        Claims.Where(c => c.Status == ClaimSupport.Conflicted).ToList();

    public double UnsupportedRatio =>
        Claims.Count == 0 ? 0 : (double)Unsupported.Count / Claims.Count;

    public double SupportedRatio =>
        Claims.Count == 0
            ? 1
            : (double)Claims.Count(c =>
                c.Status is ClaimSupport.Supported or ClaimSupport.PartiallySupported)
              / Claims.Count;
}

/// <summary>One planned query in a research plan.</summary>
public sealed record ResearchQuery(
    string Query,
    string Intent,
    string? DomainFilter = null,
    bool RequiresRecency = false,
    string? Language = null);

/// <summary>The plan a research run executes.</summary>
public sealed record ResearchPlan(
    string Question,
    IReadOnlyList<ResearchQuery> Queries,
    bool PrefersPrimarySources,
    DateTimeOffset? FreshnessFloor);

public enum CitationVerdict
{
    Valid,
    SourceMissing,
    SourceDoesNotSupportClaim,
    IrrelevantPage,
    DuplicateOnlySupport,
    OutdatedEvidence,
    QuarantinedSource
}

public sealed record CitationIssue(
    string ClaimId,
    string? SourceId,
    CitationVerdict Verdict,
    string Detail);

/// <summary>The gate that runs before an answer is allowed out.</summary>
public sealed record CitationValidationResult(
    bool Passed,
    IReadOnlyList<CitationIssue> Issues,
    int ClaimsChecked,
    int ClaimsWithValidSupport)
{
    public double SupportRatio =>
        ClaimsChecked == 0 ? 1 : (double)ClaimsWithValidSupport / ClaimsChecked;
}
