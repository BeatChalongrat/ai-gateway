using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using Xunit;

namespace SranDesktop.Tests.Agent;

public sealed class InteractionReliabilityTests
{
    [Fact]
    public void Desktop_Reference_Is_Bound_To_Exact_Observation_And_Window_Identity()
    {
        var observedAt = DateTimeOffset.UtcNow;
        var snapshot = DesktopSnapshot("obs-1", 5, 101, 42, observedAt);
        var reference = DesktopReference("obs-1", 5, 101, 42, observedAt);

        InteractionReferenceValidator.Validate(reference, snapshot, observedAt)
            .Valid.Should().BeTrue();

        var differentObservation = snapshot with { ObservationId = "obs-2" };
        var result = InteractionReferenceValidator.Validate(
            reference, differentObservation, observedAt);

        result.Valid.Should().BeFalse();
        result.FailureCode.Should().Be("ObservationIdentityChanged");
    }

    [Fact]
    public void Fresh_Desktop_Resolution_Rejects_Moved_Or_Semantically_Different_Target()
    {
        var observedAt = DateTimeOffset.UtcNow;
        var snapshot = DesktopSnapshot("obs-1", 5, 101, 42, observedAt);
        var reference = DesktopReference("obs-1", 5, 101, 42, observedAt);
        var moved = reference with { X = reference.X + 20 };

        var result = InteractionReferenceValidator.Validate(
            reference, snapshot, observedAt, moved, maximumBoundsDrift: 3);

        result.Valid.Should().BeFalse();
        result.FailureCode.Should().Be("TargetGeometryChanged");
    }

    [Fact]
    public void Browser_Reference_Is_Invalid_After_Navigation_Or_Dom_Rerender()
    {
        var observedAt = DateTimeOffset.UtcNow;
        var snapshot = BrowserSnapshot("obs-web", navigationId: 9, generation: 12);
        var reference = BrowserReference(
            "obs-web", navigationId: 9, generation: 12, observedAt);

        InteractionReferenceValidator.Validate(reference, snapshot, observedAt)
            .Valid.Should().BeTrue();

        var rerendered = snapshot with
        {
            BrowserDocument = snapshot.BrowserDocument! with { DomGeneration = 13 }
        };
        var result = InteractionReferenceValidator.Validate(
            reference, rerendered, observedAt);

        result.Valid.Should().BeFalse();
        result.FailureCode.Should().Be("StaleDomReference");
    }

    [Fact]
    public void Unknown_Outcome_Requires_Reconciliation_And_Never_Blind_Retry()
    {
        var decision = InteractionRecoveryPolicy.Decide(
            "UnknownOutcome",
            possibleSideEffect: true,
            actionSignature: "submit:order-42",
            new InteractionRecoveryState(
                1,
                0,
                2,
                1,
                DateTimeOffset.UtcNow,
                ["submit:order-42"]));

        decision.Allowed.Should().BeTrue();
        decision.Reconcile.Should().BeTrue();
        decision.NeedsUser.Should().BeTrue();
        decision.Reground.Should().BeFalse();
    }

    [Fact]
    public void Repeated_Action_Loop_Is_Terminated_By_Budget()
    {
        var decision = InteractionRecoveryPolicy.Decide(
            "StaleDomReference",
            possibleSideEffect: false,
            actionSignature: "click:send",
            new InteractionRecoveryState(
                2,
                2,
                4,
                2,
                DateTimeOffset.UtcNow,
                ["click:send", "click:send"]));

        decision.Allowed.Should().BeFalse();
        decision.FailureCode.Should().Be("RepeatedActionLoop");
    }

    [Fact]
    public void Stale_Target_Recovery_Discards_Reference_And_Regrounds()
    {
        var decision = InteractionRecoveryPolicy.Decide(
            "StaleDomReference",
            possibleSideEffect: false,
            actionSignature: "click:details",
            new InteractionRecoveryState(
                1,
                1,
                2,
                1,
                DateTimeOffset.UtcNow,
                ["click:details"]));

        decision.Allowed.Should().BeTrue();
        decision.Reobserve.Should().BeTrue();
        decision.Reground.Should().BeTrue();
        decision.Reconcile.Should().BeFalse();
    }

    private static InteractionObservationSnapshot DesktopSnapshot(
        string observationId,
        long captureVersion,
        long windowHandle,
        int processId,
        DateTimeOffset timestamp) =>
        new(
            observationId,
            "run-1",
            "step-1",
            InteractionObservationSource.Desktop,
            captureVersion,
            timestamp,
            7,
            InteractionCoordinateSpace.PhysicalScreenPixels,
            [new DisplaySurfaceObservation("primary", -1920, 0, 3840, 2160, 1.5, 1.5, true)],
            new WindowObservation(
                windowHandle, processId, "fixture.exe", "Fixture",
                10, 20, 800, 600, true, true, false),
            [],
            null,
            "uia:fixture",
            null,
            null,
            "state-1",
            ObservationTrust.ToolObservation,
            SensitiveDataClassification.Confidential,
            "metadata_only",
            true,
            []);

    private static InteractionObservationSnapshot BrowserSnapshot(
        string observationId,
        long navigationId,
        long generation) =>
        new(
            observationId,
            "run-1",
            "step-1",
            InteractionObservationSource.Browser,
            1,
            DateTimeOffset.UtcNow,
            0,
            InteractionCoordinateSpace.DomViewportCssPixels,
            [],
            null,
            [],
            new BrowserDocumentObservation(
                "browser-1", 3, "top", navigationId, generation,
                "https://example.invalid", "Fixture", "complete",
                1280, 720, 1, null),
            "a11y:3",
            null,
            null,
            "state-web",
            ObservationTrust.UntrustedExternalContent,
            SensitiveDataClassification.Confidential,
            "metadata_only",
            true,
            []);

    private static InteractionElementReference DesktopReference(
        string observationId,
        long captureVersion,
        long windowHandle,
        int processId,
        DateTimeOffset timestamp) =>
        new(
            "element-1",
            observationId,
            captureVersion,
            InteractionObservationSource.Desktop,
            "fixture.exe",
            windowHandle,
            processId,
            null,
            null,
            null,
            null,
            null,
            [],
            "button",
            "Move",
            "Move",
            100,
            120,
            80,
            30,
            "ancestor-1",
            null,
            1,
            timestamp,
            TimeSpan.FromSeconds(5));

    private static InteractionElementReference BrowserReference(
        string observationId,
        long navigationId,
        long generation,
        DateTimeOffset timestamp) =>
        new(
            "dom-1",
            observationId,
            1,
            InteractionObservationSource.Browser,
            "chrome",
            null,
            null,
            3,
            "top",
            navigationId,
            generation,
            "backend-node-12",
            ["[data-testid=details]", "role=button;name=Details"],
            "button",
            "Details",
            "Details",
            50,
            80,
            100,
            32,
            "form>section",
            null,
            .99,
            timestamp,
            TimeSpan.FromSeconds(10));
}
