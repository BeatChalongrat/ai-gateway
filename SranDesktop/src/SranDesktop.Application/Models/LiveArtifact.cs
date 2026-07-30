namespace SranDesktop.Application.Models;

/// <summary>
/// Where a live artifact's data comes from, and therefore what "refresh" means.
/// </summary>
public enum LiveSourceKind
{
    /// <summary>Read on a timer. Always available; the baseline everything else falls back to.</summary>
    Polling,

    /// <summary>An external system posts changes to a local endpoint.</summary>
    Webhook,

    /// <summary>A long-lived subscription pushes changes (e.g. an MCP notification).</summary>
    Subscription
}

/// <summary>
/// The lifecycle of a live artifact.
///
/// <see cref="Stale"/> is a first-class state rather than an error: an artifact
/// whose source has stopped answering still holds the last good data, and the
/// honest thing to show is that data plus the fact that it is old. Silently
/// serving it as current is how a dashboard reports a dead feed as healthy.
/// </summary>
public enum LiveArtifactState
{
    Created,
    Refreshing,
    Current,
    Stale,
    SourceUnavailable,
    CredentialExpired,
    PermissionRevoked,
    ValidationFailed,
    NeedsUser,
    Disabled
}

/// <summary>One immutable version of a live artifact's content.</summary>
public sealed record LiveArtifactVersion(
    int Version,
    string ContentHash,
    string Content,
    DateTimeOffset ProducedUtc,
    string SourceProvenance,
    string? IdempotencyKey = null);

/// <summary>
/// A live artifact: content that keeps itself up to date from a source.
/// </summary>
public sealed record LiveArtifact
{
    public required string ArtifactId { get; init; }
    public required string Name { get; init; }
    public required LiveSourceKind SourceKind { get; init; }

    /// <summary>What is being watched — a URL, a connector tool, a query.</summary>
    public required string SourceReference { get; init; }

    /// <summary>Connector/account this artifact reads through, when it has one.</summary>
    public string? ConnectorId { get; init; }
    public string? AccountKey { get; init; }

    public LiveArtifactState State { get; init; } = LiveArtifactState.Created;
    public string? StateReason { get; init; }

    public int CurrentVersion { get; init; }
    public string? CurrentContentHash { get; init; }

    public DateTimeOffset? LastSuccessfulRefreshUtc { get; init; }
    public DateTimeOffset? LastAttemptedRefreshUtc { get; init; }
    public DateTimeOffset? NextRefreshUtc { get; init; }

    /// <summary>How long after a successful refresh the content is still "current".</summary>
    public TimeSpan StaleAfter { get; init; } = TimeSpan.FromMinutes(30);
    public TimeSpan PollInterval { get; init; } = TimeSpan.FromMinutes(15);

    /// <summary>Versions kept. Older ones are pruned so history cannot grow without bound.</summary>
    public int RetainedVersions { get; init; } = 10;

    public bool RefreshInProgress { get; init; }
    public int ConsecutiveFailures { get; init; }

    /// <summary>The run that created this artifact, for provenance across capabilities.</summary>
    public string? OriginRunId { get; init; }

    public DateTimeOffset CreatedUtc { get; init; } = DateTimeOffset.UtcNow;

    /// <summary>True when the artifact holds data that is no longer guaranteed fresh.</summary>
    public bool IsServingStaleData(DateTimeOffset now) =>
        LastSuccessfulRefreshUtc is { } last && now - last > StaleAfter;
}

/// <summary>What a source returned on one refresh attempt.</summary>
public sealed record LiveRefreshOutcome(
    bool Succeeded,
    string? Content,
    string? Provenance,
    LiveArtifactState? FailureState = null,
    string? FailureReason = null,
    string? IdempotencyKey = null)
{
    public static LiveRefreshOutcome Ok(
        string content, string provenance, string? idempotencyKey = null) =>
        new(true, content, provenance, IdempotencyKey: idempotencyKey);

    public static LiveRefreshOutcome Failed(LiveArtifactState state, string reason) =>
        new(false, null, null, state, reason);
}

/// <summary>The result of applying a refresh to an artifact.</summary>
public sealed record LiveRefreshResult(
    LiveArtifact Artifact,
    LiveArtifactVersion? NewVersion,
    bool ContentChanged,
    bool Suppressed,
    string Summary)
{
    /// <summary>True when a duplicate or out-of-order event was correctly ignored.</summary>
    public bool WasDeduplicated => Suppressed;
}
