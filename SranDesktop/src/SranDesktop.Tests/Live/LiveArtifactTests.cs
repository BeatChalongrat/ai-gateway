using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Live;

/// <summary>
/// A live artifact is only useful if its state can be believed, so these tests
/// are about the ways it could lie: claiming to be current while holding old
/// data, inventing versions when nothing changed, applying the same event twice,
/// or quietly resuming after a permission was taken away.
/// </summary>
public sealed class LiveArtifactTests
{
    private static readonly DateTimeOffset Start = new(2026, 7, 29, 9, 0, 0, TimeSpan.Zero);

    [Fact]
    public void A_Changed_Source_Increments_The_Version()
    {
        var artifact = New();
        var (refreshing, started) = LiveArtifactController.BeginRefresh(artifact, Start);
        started.Should().BeTrue();
        refreshing.State.Should().Be(LiveArtifactState.Refreshing);

        var result = LiveArtifactController.ApplyRefresh(
            refreshing, LiveRefreshOutcome.Ok("rows: 42", "mcp:reports/list"), Start);

        result.ContentChanged.Should().BeTrue();
        result.NewVersion!.Version.Should().Be(1);
        result.NewVersion.SourceProvenance.Should().Be("mcp:reports/list");
        result.Artifact.State.Should().Be(LiveArtifactState.Current);
        result.Artifact.CurrentVersion.Should().Be(1);
    }

    [Fact]
    public void An_Unchanged_Source_Does_Not_Create_A_Version()
    {
        var artifact = Refreshed(New(), "rows: 42", Start);

        var again = LiveArtifactController.ApplyRefresh(
            artifact, LiveRefreshOutcome.Ok("rows: 42", "poll"), Start.AddMinutes(15));

        again.ContentChanged.Should().BeFalse();
        again.NewVersion.Should().BeNull(
            "a version number that increments on every poll stops meaning anything");
        again.Artifact.CurrentVersion.Should().Be(1);
        again.Artifact.State.Should().Be(LiveArtifactState.Current);
        again.Artifact.LastSuccessfulRefreshUtc.Should().Be(Start.AddMinutes(15),
            "the source was still successfully contacted");
    }

    [Fact]
    public void A_Duplicate_Event_Is_Suppressed()
    {
        var artifact = Refreshed(New(), "v1", Start);
        var applied = new[] { "evt-1" };

        var duplicate = LiveArtifactController.ApplyRefresh(
            artifact,
            LiveRefreshOutcome.Ok("v2-different", "webhook", idempotencyKey: "evt-1"),
            Start.AddMinutes(1),
            applied);

        duplicate.WasDeduplicated.Should().BeTrue();
        duplicate.NewVersion.Should().BeNull();
        duplicate.Artifact.CurrentVersion.Should().Be(1,
            "at-least-once delivery must become effectively-once here");
    }

    [Fact]
    public void An_Out_Of_Order_Event_Is_Not_Applied()
    {
        var artifact = Refreshed(New(), "v1", Start) with { CurrentVersion = 5 };

        LiveArtifactController.ShouldApplyEvent(artifact, eventVersion: 4, null, [])
            .Should().BeFalse("an older version arriving late must not move the artifact backwards");
        LiveArtifactController.ShouldApplyEvent(artifact, eventVersion: 5, null, [])
            .Should().BeFalse();
        LiveArtifactController.ShouldApplyEvent(artifact, eventVersion: 6, null, [])
            .Should().BeTrue();
    }

    [Fact]
    public void Content_Goes_Stale_On_Its_Own()
    {
        var artifact = Refreshed(New(), "v1", Start);

        var soon = LiveArtifactController.Reassess(artifact, Start.AddMinutes(5));
        var later = LiveArtifactController.Reassess(artifact, Start.AddHours(3));

        soon.State.Should().Be(LiveArtifactState.Current);
        later.State.Should().Be(LiveArtifactState.Stale,
            "holding week-old data while reporting Current is the failure this state exists for");
        later.StateReason.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public void A_Source_Outage_Keeps_The_Last_Good_Version()
    {
        var artifact = Refreshed(New(), "good data", Start);

        var failed = LiveArtifactController.ApplyRefresh(
            artifact,
            LiveRefreshOutcome.Failed(LiveArtifactState.SourceUnavailable, "connection refused"),
            Start.AddMinutes(15));

        failed.Artifact.State.Should().Be(LiveArtifactState.SourceUnavailable);
        failed.Artifact.CurrentVersion.Should().Be(1, "the last good content is still what we have");
        failed.Artifact.ConsecutiveFailures.Should().Be(1);
        failed.Artifact.NextRefreshUtc.Should().NotBeNull("transient failures are retried");
    }

    [Fact]
    public void Repeated_Failures_Back_Off()
    {
        var artifact = Refreshed(New(), "v1", Start);
        var gaps = new List<TimeSpan>();

        for (var attempt = 0; attempt < 4; attempt++)
        {
            var now = Start.AddHours(attempt + 1);
            var result = LiveArtifactController.ApplyRefresh(
                artifact, LiveRefreshOutcome.Failed(LiveArtifactState.SourceUnavailable, "down"), now);
            artifact = result.Artifact;
            gaps.Add(result.Artifact.NextRefreshUtc!.Value - now);
        }

        gaps.Should().BeInAscendingOrder("a source that is down should be polled less, not more");
        gaps.Last().Should().BeLessThanOrEqualTo(TimeSpan.FromHours(1));
    }

    [Fact]
    public void Credential_Expiry_Stops_Scheduling_And_Asks_For_A_Person()
    {
        var artifact = Refreshed(New(), "v1", Start);

        var expired = LiveArtifactController.ApplyRefresh(
            artifact,
            LiveRefreshOutcome.Failed(LiveArtifactState.CredentialExpired, "token expired"),
            Start.AddMinutes(15));

        expired.Artifact.State.Should().Be(LiveArtifactState.CredentialExpired);
        expired.Artifact.NextRefreshUtc.Should().BeNull(
            "retrying on a timer will never fix an expired credential");
    }

    [Fact]
    public void A_Reconnected_Credential_Resumes_As_Stale_Not_Current()
    {
        var artifact = Refreshed(New(), "v1", Start);
        var expired = LiveArtifactController.ApplyRefresh(
            artifact,
            LiveRefreshOutcome.Failed(LiveArtifactState.CredentialExpired, "token expired"),
            Start.AddMinutes(15)).Artifact;

        var resumed = LiveArtifactController.Resume(expired, Start.AddHours(1));

        resumed.State.Should().Be(LiveArtifactState.Stale,
            "a working credential says nothing about whether the content is up to date");
        resumed.NextRefreshUtc.Should().Be(Start.AddHours(1));
        resumed.ConsecutiveFailures.Should().Be(0);
    }

    [Fact]
    public void Connector_Revocation_Stops_Refreshing_And_Does_Not_Silently_Resume()
    {
        var artifact = Refreshed(New(), "v1", Start);

        var revoked = LiveArtifactController.Revoke(artifact, "The user revoked the connector.");

        revoked.State.Should().Be(LiveArtifactState.PermissionRevoked);
        revoked.NextRefreshUtc.Should().BeNull();
        LiveArtifactController.Reassess(revoked, Start.AddDays(1)).State
            .Should().Be(LiveArtifactState.PermissionRevoked,
                "a revocation that decays into Stale would start refreshing again");
    }

    [Fact]
    public void A_Disabled_Artifact_Refuses_To_Refresh()
    {
        var artifact = LiveArtifactController.Disable(Refreshed(New(), "v1", Start), "paused");

        var (_, started) = LiveArtifactController.BeginRefresh(artifact, Start.AddHours(1));

        started.Should().BeFalse();
        LiveArtifactController.ShouldApplyEvent(artifact, 99, "evt-9", []).Should().BeFalse();
    }

    [Fact]
    public void Two_Concurrent_Refreshes_Are_Refused()
    {
        var artifact = New();
        var (first, startedFirst) = LiveArtifactController.BeginRefresh(artifact, Start);
        var (_, startedSecond) = LiveArtifactController.BeginRefresh(first, Start);

        startedFirst.Should().BeTrue();
        startedSecond.Should().BeFalse(
            "two in-flight refreshes could publish the older read as the newer version");
    }

    [Fact]
    public void A_Manual_Refresh_Works_Alongside_The_Schedule()
    {
        var artifact = Refreshed(New(), "v1", Start);

        var manual = LiveArtifactController.ApplyRefresh(
            artifact, LiveRefreshOutcome.Ok("v2", "manual"), Start.AddMinutes(2));
        var scheduled = LiveArtifactController.ApplyRefresh(
            manual.Artifact, LiveRefreshOutcome.Ok("v2", "poll"), Start.AddMinutes(17));

        manual.NewVersion!.Version.Should().Be(2);
        scheduled.NewVersion.Should().BeNull("the scheduled poll found what the manual one fetched");
        scheduled.Artifact.CurrentVersion.Should().Be(2);
    }

    [Fact]
    public void History_Is_Bounded()
    {
        var versions = Enumerable.Range(1, 25)
            .Select(index => new LiveArtifactVersion(
                index, $"hash{index}", $"content {index}", Start.AddMinutes(index), "poll"))
            .ToList();

        var pruned = LiveArtifactController.Prune(versions, retain: 10);

        pruned.Should().HaveCount(10);
        pruned[0].Version.Should().Be(25, "newest first");
        pruned.Last().Version.Should().Be(16);
    }

    [Fact]
    public void Validation_Failure_Is_Distinct_From_An_Outage()
    {
        var artifact = Refreshed(New(), "v1", Start);

        var invalid = LiveArtifactController.ApplyRefresh(
            artifact,
            LiveRefreshOutcome.Failed(LiveArtifactState.ValidationFailed, "schema mismatch"),
            Start.AddMinutes(15));

        invalid.Artifact.State.Should().Be(LiveArtifactState.ValidationFailed);
        invalid.Artifact.CurrentVersion.Should().Be(1,
            "content that failed validation must not replace content that passed");
        LiveArtifactController.Reassess(invalid.Artifact, Start.AddDays(1)).State
            .Should().Be(LiveArtifactState.ValidationFailed);
    }

    // ── Fixtures ────────────────────────────────────────────────────────────

    private static LiveArtifact New() => new()
    {
        ArtifactId = "artifact-1",
        Name = "Quarterly report feed",
        SourceKind = LiveSourceKind.Polling,
        SourceReference = "mcp://reports/list",
        ConnectorId = "reports",
        StaleAfter = TimeSpan.FromMinutes(30),
        PollInterval = TimeSpan.FromMinutes(15),
        OriginRunId = "run-1"
    };

    private static LiveArtifact Refreshed(
        LiveArtifact artifact, string content, DateTimeOffset now) =>
        LiveArtifactController.ApplyRefresh(
            artifact, LiveRefreshOutcome.Ok(content, "poll"), now).Artifact;
}
