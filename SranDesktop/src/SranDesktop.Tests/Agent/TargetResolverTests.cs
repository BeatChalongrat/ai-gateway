using FluentAssertions;
using SranDesktop.Infrastructure.Native;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class TargetResolverTests
{
    private static readonly DateTimeOffset Now = DateTimeOffset.UtcNow;

    [Fact]
    public void Semantic_Targets_Precede_Vision_And_Raw_Pixel()
    {
        var result = TargetResolver.Resolve(new[]
        {
            Candidate(TargetMethod.VisionBoundingBox, "vision"),
            Candidate(TargetMethod.WindowsUia, "uia"),
            Candidate(TargetMethod.BrowserDom, "dom")
        }, Now);

        result.Target!.Method.Should().Be(TargetMethod.BrowserDom);
    }

    [Fact]
    public void Multiple_Matches_Do_Not_Select_Randomly()
    {
        var result = TargetResolver.Resolve(new[]
        {
            Candidate(TargetMethod.WindowsUia, "one"),
            Candidate(TargetMethod.WindowsUia, "two")
        }, Now);

        result.Resolved.Should().BeFalse();
        result.FailureCode.Should().Be("AmbiguousTarget");
        result.RequiresUserClarification.Should().BeTrue();
    }

    [Fact]
    public void Unnamed_Accessibility_Container_Does_Not_Match_Every_Text_Intent()
    {
        var intent = TargetIntentParser.Create(
            "visual target",
            "fixture",
            null,
            "click",
            "activated",
            [],
            [],
            riskLevel: TargetRiskLevel.Low);
        var unnamed = Candidate(TargetMethod.WindowsUia, "pane") with
        {
            AccessibleName = "",
            Role = "pane"
        };

        TargetScorer.Score(intent, unnamed).SemanticScore.Should().Be(0);
    }

    [Fact]
    public void Stale_Element_Is_Rejected()
    {
        var result = TargetResolver.Resolve(
            new[] { Candidate(TargetMethod.WindowsUia, "old") with
                { ResolvedAtUtc = Now.AddSeconds(-4) } },
            Now);

        result.FailureCode.Should().Be("TargetNotActionable");
    }

    [Fact]
    public void Raw_Pixel_Requires_Explicit_Fallback_Gate()
    {
        TargetResolver.Resolve(
            new[] { Candidate(TargetMethod.RawPixel, "point") }, Now)
            .Resolved.Should().BeFalse();
    }

    [Fact]
    public void Vision_Bounding_Box_Maps_And_Uses_Interior_Point()
    {
        var capture = Capture();
        var target = new VisionTarget(
            capture.CaptureId, "Mute", new ImageRect(100, 50, 40, 20), .95,
            "unmuted", Array.Empty<string>(), "Unique microphone control",
            "click", "muted");

        var result = VisionTargetMapper.Map(capture, target, Now);

        result.Target!.BoundingBox.Should().Be(new PhysicalRect(-800, 100, 80, 40));
        VisionTargetMapper.SafePoint(result.Target.BoundingBox).Should().Be((-760, 120));
    }

    [Fact]
    public void Low_Confidence_And_Old_Capture_Id_Are_Rejected()
    {
        var capture = Capture();
        var low = new VisionTarget(
            capture.CaptureId, "x", new ImageRect(1, 1, 10, 10), .5,
            null, Array.Empty<string>(), "weak", "click", "on");
        VisionTargetMapper.Map(capture, low, Now).FailureCode.Should().Be("LowConfidence");

        var stale = low with { CaptureId = "other", Confidence = .99 };
        VisionTargetMapper.Map(capture, stale, Now).FailureCode.Should().Be("StaleVisionTarget");
    }

    private static TargetCandidate Candidate(TargetMethod method, string id) =>
        new(method, id, "button", id, 10, 42,
            new PhysicalRect(10, 10, 100, 40), .99, null, "invoke",
            new[] { "test" }, Now);

    private static CaptureContext Capture() =>
        new("capture", Now, CaptureType.Window, 42, 10, "Test", "m1",
            CoordinateSpace.WindowImagePixels, 1000, 500,
            new PhysicalRect(-1000, 0, 2000, 1000),
            1.25, 1.25, 1, 1);
}
