using System.Security.Cryptography;
using System.Text;

namespace SranDesktop.Application.Services;

using SranDesktop.Application.Models;

/// <summary>
/// The state machine behind a live artifact.
///
/// It is written as a pure transition function on purpose: the whole value of a
/// live artifact is that its state is trustworthy, and state that is mutated in
/// place from several call sites — a poll timer, a webhook, a manual refresh,
/// a credential expiry — is exactly the kind that ends up claiming "current"
/// while holding week-old data. Every path produces a new artifact record from
/// an old one plus one outcome, so there is one place to read and one place to
/// get wrong.
///
/// Two rules carry most of the weight:
///
/// A refresh that returns unchanged content does NOT create a version. Version
/// numbers mean "the content changed", and a poller that bumps the version every
/// 15 minutes makes them mean nothing.
///
/// A repeat of an event already applied is suppressed rather than re-applied.
/// At-least-once delivery is normal for webhooks and subscriptions, so the
/// artifact has to be the thing that makes it effectively-once.
/// </summary>
public static class LiveArtifactController
{
    public static string ComputeHash(string content) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(content ?? "")));

    /// <summary>Marks an artifact as refreshing. Rejects a second concurrent refresh.</summary>
    public static (LiveArtifact Artifact, bool Started) BeginRefresh(
        LiveArtifact artifact, DateTimeOffset now)
    {
        ArgumentNullException.ThrowIfNull(artifact);

        if (artifact.State == LiveArtifactState.Disabled)
            return (artifact, false);

        // One refresh at a time. Two in flight would race to write the version
        // and could publish the older read as the newer version.
        if (artifact.RefreshInProgress)
            return (artifact, false);

        return (artifact with
        {
            RefreshInProgress = true,
            State = LiveArtifactState.Refreshing,
            LastAttemptedRefreshUtc = now
        }, true);
    }

    /// <summary>
    /// Applies a refresh outcome, producing the artifact's next state and — only
    /// when the content actually changed — a new version.
    /// </summary>
    /// <param name="appliedIdempotencyKeys">
    /// Keys already applied to this artifact. A repeat is suppressed.
    /// </param>
    public static LiveRefreshResult ApplyRefresh(
        LiveArtifact artifact,
        LiveRefreshOutcome outcome,
        DateTimeOffset now,
        IReadOnlyCollection<string>? appliedIdempotencyKeys = null)
    {
        ArgumentNullException.ThrowIfNull(artifact);
        ArgumentNullException.ThrowIfNull(outcome);

        if (!outcome.Succeeded)
            return Failure(artifact, outcome, now);

        // At-least-once delivery: the same event arriving twice must not create
        // two versions of the artifact.
        if (outcome.IdempotencyKey is { Length: > 0 } key
            && appliedIdempotencyKeys is not null
            && appliedIdempotencyKeys.Contains(key))
        {
            return new LiveRefreshResult(
                artifact with
                {
                    RefreshInProgress = false,
                    State = LiveArtifactState.Current,
                    LastAttemptedRefreshUtc = now,
                    NextRefreshUtc = now + artifact.PollInterval
                },
                NewVersion: null,
                ContentChanged: false,
                Suppressed: true,
                Summary: $"Event {key} was already applied; no new version.");
        }

        var content = outcome.Content ?? "";
        var hash = ComputeHash(content);

        // Nothing changed. The artifact is confirmed current — that is the
        // useful outcome — but the version stays where it is.
        if (string.Equals(hash, artifact.CurrentContentHash, StringComparison.Ordinal))
        {
            return new LiveRefreshResult(
                artifact with
                {
                    RefreshInProgress = false,
                    State = LiveArtifactState.Current,
                    StateReason = null,
                    ConsecutiveFailures = 0,
                    LastSuccessfulRefreshUtc = now,
                    LastAttemptedRefreshUtc = now,
                    NextRefreshUtc = now + artifact.PollInterval
                },
                NewVersion: null,
                ContentChanged: false,
                Suppressed: false,
                Summary: "Source confirmed unchanged; version unchanged.");
        }

        var version = new LiveArtifactVersion(
            artifact.CurrentVersion + 1,
            hash,
            content,
            now,
            outcome.Provenance ?? artifact.SourceReference,
            outcome.IdempotencyKey);

        return new LiveRefreshResult(
            artifact with
            {
                RefreshInProgress = false,
                State = LiveArtifactState.Current,
                StateReason = null,
                ConsecutiveFailures = 0,
                CurrentVersion = version.Version,
                CurrentContentHash = hash,
                LastSuccessfulRefreshUtc = now,
                LastAttemptedRefreshUtc = now,
                NextRefreshUtc = now + artifact.PollInterval
            },
            version,
            ContentChanged: true,
            Suppressed: false,
            Summary: $"Content changed; version {version.Version}.");
    }

    private static LiveRefreshResult Failure(
        LiveArtifact artifact, LiveRefreshOutcome outcome, DateTimeOffset now)
    {
        var state = outcome.FailureState ?? LiveArtifactState.SourceUnavailable;

        // A credential or permission problem is not a transient outage: retrying
        // on a timer will never fix it and only burns the account's rate limit,
        // so the artifact stops scheduling and asks for a person.
        var needsPerson = state is LiveArtifactState.CredentialExpired
            or LiveArtifactState.PermissionRevoked
            or LiveArtifactState.NeedsUser;

        return new LiveRefreshResult(
            artifact with
            {
                RefreshInProgress = false,
                State = state,
                StateReason = outcome.FailureReason,
                ConsecutiveFailures = artifact.ConsecutiveFailures + 1,
                LastAttemptedRefreshUtc = now,
                NextRefreshUtc = needsPerson ? null : now + Backoff(artifact.ConsecutiveFailures + 1, artifact.PollInterval)
            },
            NewVersion: null,
            ContentChanged: false,
            Suppressed: false,
            Summary: outcome.FailureReason ?? state.ToString());
    }

    /// <summary>
    /// Exponential backoff capped at an hour. A source that is down stays down
    /// for a while; polling it every 15 seconds helps nobody and looks like an
    /// attack from the other end.
    /// </summary>
    private static TimeSpan Backoff(int consecutiveFailures, TimeSpan baseInterval)
    {
        var multiplier = Math.Min(1 << Math.Min(consecutiveFailures, 6), 64);
        var delay = TimeSpan.FromTicks(baseInterval.Ticks * multiplier);
        return delay > TimeSpan.FromHours(1) ? TimeSpan.FromHours(1) : delay;
    }

    /// <summary>
    /// Re-evaluates freshness without contacting the source. Called when an
    /// artifact is read, so a stale one presents as stale even if no refresh
    /// has been attempted since it went out of date.
    /// </summary>
    public static LiveArtifact Reassess(LiveArtifact artifact, DateTimeOffset now)
    {
        ArgumentNullException.ThrowIfNull(artifact);

        if (artifact.State is LiveArtifactState.Disabled
            or LiveArtifactState.CredentialExpired
            or LiveArtifactState.PermissionRevoked
            or LiveArtifactState.NeedsUser
            or LiveArtifactState.ValidationFailed)
            return artifact;

        if (artifact.State == LiveArtifactState.Current && artifact.IsServingStaleData(now))
            return artifact with
            {
                State = LiveArtifactState.Stale,
                StateReason =
                    $"No successful refresh since {artifact.LastSuccessfulRefreshUtc:u}."
            };

        return artifact;
    }

    /// <summary>
    /// Applies a connector revocation. Distinct from a failed refresh because it
    /// is a policy decision, not an outage: the artifact must stop refreshing and
    /// say why, and it must not quietly resume if the source starts answering.
    /// </summary>
    public static LiveArtifact Revoke(LiveArtifact artifact, string reason) =>
        artifact with
        {
            State = LiveArtifactState.PermissionRevoked,
            StateReason = reason,
            RefreshInProgress = false,
            NextRefreshUtc = null
        };

    public static LiveArtifact Disable(LiveArtifact artifact, string reason) =>
        artifact with
        {
            State = LiveArtifactState.Disabled,
            StateReason = reason,
            RefreshInProgress = false,
            NextRefreshUtc = null
        };

    /// <summary>
    /// Re-enables after a credential is reconnected. Returns to Stale, never to
    /// Current: the credential being valid again says nothing about whether the
    /// content is up to date, and only a real refresh can establish that.
    /// </summary>
    public static LiveArtifact Resume(LiveArtifact artifact, DateTimeOffset now) =>
        artifact with
        {
            State = artifact.CurrentVersion == 0
                ? LiveArtifactState.Created
                : LiveArtifactState.Stale,
            StateReason = null,
            ConsecutiveFailures = 0,
            NextRefreshUtc = now
        };

    /// <summary>Keeps history bounded, newest first.</summary>
    public static IReadOnlyList<LiveArtifactVersion> Prune(
        IEnumerable<LiveArtifactVersion> versions, int retain)
    {
        ArgumentNullException.ThrowIfNull(versions);
        return versions
            .OrderByDescending(version => version.Version)
            .Take(Math.Max(1, retain))
            .ToList();
    }

    /// <summary>
    /// Decides whether an inbound event should be applied at all.
    ///
    /// Out-of-order delivery is the case worth naming: an event carrying a
    /// version at or below the current one is a replay of history, and applying
    /// it would move the artifact backwards while looking like an update.
    /// </summary>
    public static bool ShouldApplyEvent(
        LiveArtifact artifact,
        int? eventVersion,
        string? idempotencyKey,
        IReadOnlyCollection<string> appliedKeys)
    {
        ArgumentNullException.ThrowIfNull(artifact);
        ArgumentNullException.ThrowIfNull(appliedKeys);

        if (artifact.State == LiveArtifactState.Disabled)
            return false;
        if (idempotencyKey is { Length: > 0 } && appliedKeys.Contains(idempotencyKey))
            return false;
        if (eventVersion is { } version && version <= artifact.CurrentVersion)
            return false;
        return true;
    }
}
