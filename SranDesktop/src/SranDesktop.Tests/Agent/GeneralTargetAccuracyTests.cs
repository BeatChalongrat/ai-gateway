using System.Text.Json;
using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Native;
using SranDesktop.Infrastructure.Native.Uia;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class GeneralTargetAccuracyTests
{
    private static readonly DateTimeOffset Now = DateTimeOffset.UtcNow;

    [Fact]
    public void Exact_Accessible_Name_Outscores_Partial_Match()
    {
        var intent = TargetIntentParser.Create("Send", role: "button");
        var exact = Score(intent, Candidate("send", "Send"));
        var partial = Score(intent, Candidate("send-later", "Send later"));

        exact.SemanticScore.Should().Be(1);
        exact.FinalScore.Should().BeGreaterThan(partial.FinalScore);
    }

    [Theory]
    [InlineData("ส่ง", "Send")]
    [InlineData("send", "ส่งข้อความ")]
    [InlineData("ปิดไมค์", "Mute")]
    public void Thai_And_English_Synonyms_Match(string request, string accessibleName)
    {
        var intent = TargetIntentParser.Create(request);

        Score(intent, Candidate("target", accessibleName)).SemanticScore
            .Should().BeGreaterThan(0);
    }

    [Fact]
    public void Role_Mismatch_Is_Rejected_By_Risk_Threshold()
    {
        var intent = TargetIntentParser.Create(
            "Send", role: "button", riskLevel: TargetRiskLevel.High);
        var wrongRole = Candidate("send-edit", "Send") with
        {
            Role = "edit",
            ControlType = "edit",
            Method = TargetMethod.VisionBoundingBox
        };

        TargetResolver.Resolve(intent, new[] { wrongRole }, Now).Resolution.Resolved
            .Should().BeFalse();
    }

    [Theory]
    [InlineData(false, true, false)]
    [InlineData(true, true, true)]
    public void Disabled_And_Offscreen_Candidates_Are_Rejected(
        bool enabled, bool visible, bool offscreen)
    {
        var candidate = Candidate("target", "Send") with
        {
            IsEnabled = enabled,
            IsVisible = visible,
            IsOffscreen = offscreen
        };

        TargetResolver.Resolve(
            TargetIntentParser.Create("Send"), new[] { candidate }, Now)
            .Resolution.Resolved.Should().BeFalse();
    }

    [Fact]
    public void Multiple_Exact_Name_Candidates_Fail_Score_Gap()
    {
        var intent = TargetIntentParser.Create("Close", role: "button");
        var result = TargetResolver.Resolve(intent, new[]
        {
            Candidate("dialog-close", "Close"),
            Candidate("window-close", "Close")
        }, Now).Resolution;

        result.FailureCode.Should().Be("AmbiguousTarget");
    }

    [Fact]
    public void Uia_And_Vision_Boxes_Fuse_When_Semantics_Agree()
    {
        var uia = Candidate("uia", "Mute") with
        {
            BoundingBox = new PhysicalRect(80, 900, 32, 32),
            Method = TargetMethod.WindowsUia
        };
        var vision = Candidate("vision", "microphone Mute") with
        {
            BoundingBox = new PhysicalRect(78, 898, 36, 36),
            Method = TargetMethod.VisionBoundingBox
        };

        var fused = CandidateFusion.Fuse(new[] { vision, uia }, .5);

        fused.Should().ContainSingle();
        fused[0].Method.Should().Be(TargetMethod.WindowsUia);
        fused[0].ValidationEvidence.Should().Contain(e => e.StartsWith("fused:"));
    }

    [Fact]
    public void Uia_And_Vision_Disagreement_Remains_Ambiguous_Candidates()
    {
        var uia = Candidate("uia", "Headphones") with
        {
            BoundingBox = new PhysicalRect(80, 900, 32, 32)
        };
        var vision = Candidate("vision", "Microphone") with
        {
            BoundingBox = new PhysicalRect(78, 898, 36, 36),
            Method = TargetMethod.VisionBoundingBox
        };

        CandidateFusion.Fuse(new[] { uia, vision }, .5).Should().HaveCount(2);
    }

    [Fact]
    public void Safe_Point_Prefers_Uia_Clickable_Point()
    {
        var target = Candidate("target", "Send") with
        {
            ClickablePoint = new System.Windows.Point(30, 25)
        };

        SafeClickPointSelector.Select(target, Array.Empty<TargetCandidate>())
            .Should().Match<SafeClickPointResult>(r =>
                r.Selected && r.X == 30 && r.Y == 25 && r.Method == "uia_clickable_point");
    }

    [Fact]
    public void Safe_Point_Avoids_Adjacent_Icon()
    {
        var target = Candidate("mic", "Microphone") with
        {
            BoundingBox = new PhysicalRect(0, 0, 30, 30)
        };
        var neighbor = Candidate("headphones", "Headphones") with
        {
            BoundingBox = new PhysicalRect(16, 0, 30, 30)
        };

        var point = SafeClickPointSelector.Select(target, new[] { neighbor }, 3);

        point.Selected.Should().BeTrue();
        neighbor.BoundingBox.Contains(point.X, point.Y).Should().BeFalse();
    }

    [Fact]
    public void Safe_Point_Ignores_Non_Actionable_Ancestor_Pane()
    {
        var target = Candidate("visual", "visual target") with
        {
            Method = TargetMethod.VisionBoundingBox,
            Role = "visual",
            BoundingBox = new PhysicalRect(100, 100, 80, 40),
            SupportedActions = null
        };
        var ancestor = Candidate("pane", "") with
        {
            Role = "pane",
            BoundingBox = new PhysicalRect(0, 0, 800, 600),
            SupportedActions = []
        };

        SafeClickPointSelector.Select(target, [ancestor]).Selected.Should().BeTrue();
    }

    [Fact]
    public void Malformed_Vision_Result_Does_Not_Fall_Back_To_Point()
    {
        using var args = JsonDocument.Parse(
            """{"vision_target":{"label":"Send","x":1,"y":2}}""");

        ComputerClickTool.TryParseVisionTarget(
            args.RootElement, "capture", out var target, out var error)
            .Should().BeFalse();
        target.Should().BeNull();
        error.Should().StartWith("VisionTargetParseFailed");
    }

    [Fact]
    public async Task Semantic_ComputerClick_Uses_Runtime_Caller_And_Verified_Result()
    {
        var screen = new ScreenContext();
        var capture = Capture();
        screen.Set(capture);
        var runtime = new FakeTargetRuntime(completed: true);
        var metrics = new ComputerUseMetrics();
        var tool = new ComputerClickTool(screen, targets: runtime, metrics: metrics);
        using var args = JsonDocument.Parse(
            $$"""{"capture_id":"{{capture.CaptureId}}","target_description":"Send","role":"button"}""");

        var result = await tool.ExecuteAsync(args.RootElement, CancellationToken.None);

        runtime.ResolveCalls.Should().Be(1);
        runtime.ActivateCalls.Should().Be(1);
        result.ComputerAction!.TargetActivated.Should().BeTrue();
        result.ComputerAction.TaskCompleted.Should().BeTrue();
        metrics.Get(ComputerUseMetric.SemanticTarget).Should().Be(1);
        metrics.Get(ComputerUseMetric.TaskCompleted).Should().Be(1);
    }

    [Fact]
    public async Task Activation_Failure_Cannot_Report_Task_Completion()
    {
        var screen = new ScreenContext();
        var capture = Capture();
        screen.Set(capture);
        var tool = new ComputerClickTool(
            screen, targets: new FakeTargetRuntime(completed: false));
        using var args = JsonDocument.Parse(
            $$"""{"capture_id":"{{capture.CaptureId}}","target_description":"Send"}""");

        var result = await tool.ExecuteAsync(args.RootElement, CancellationToken.None);

        result.Success.Should().BeFalse();
        result.ComputerAction!.TargetActivated.Should().BeFalse();
        result.ComputerAction.TaskCompleted.Should().BeFalse();
        result.ComputerAction.Evidence.Should()
            .Contain(e => e.Contains("requires_fresh_observation=True"));
    }

    [Fact]
    public void ComputerClick_Schema_Prefers_Semantic_And_Retains_Legacy_Coordinates()
    {
        var schema = new ComputerClickTool(new ScreenContext()).ParametersSchema;

        schema.GetProperty("properties").TryGetProperty("target_description", out _)
            .Should().BeTrue();
        schema.GetProperty("properties").TryGetProperty("vision_target", out _)
            .Should().BeTrue();
        schema.GetProperty("properties").TryGetProperty("x", out _).Should().BeTrue();
        schema.GetProperty("properties").TryGetProperty("y", out _).Should().BeTrue();
        schema.TryGetProperty("anyOf", out _).Should().BeFalse();
    }

    // ── Phase 1 (UIA-first): UIA-point-first gate + fallback seam ──

    [Theory]
    [InlineData("left", "single", true)]    // only a plain single left click gets pattern-first
    [InlineData("left", "double", false)]   // double-click has no pattern equivalent
    [InlineData("left", "triple", false)]
    [InlineData("right", "single", false)]  // context menu — physical only
    [InlineData("middle", "single", false)]
    public void PatternFirst_Only_For_Single_Left_Click(string button, string clickType, bool expected)
        => ComputerClickTool.ShouldTryPatternFirst(button, clickType).Should().Be(expected);

    [Fact]
    public async Task Point_Activation_Seam_Reports_No_Pattern_By_Default()
    {
        // The fallback contract: when nothing actionable sits under the point, the seam
        // reports TargetActivated=false so ComputerClickTool proceeds to the coordinate click.
        var runtime = new FakeTargetRuntime(completed: false);
        var capture = Capture();

        var result = await runtime.TryActivateAtPointAsync(capture, 5, 5, CancellationToken.None);

        runtime.PointActivateCalls.Should().Be(1);
        result.TargetActivated.Should().BeFalse();
        result.FailureCode.Should().Be("NoPatternAtPoint");
    }

    [Fact]
    public void Benchmark_Uses_Ground_Truth_Ids_And_Detects_False_Success()
    {
        var bounds = new PhysicalRect(0, 0, 100, 40);
        var attempts = new[]
        {
            Attempt("correct", "correct", bounds, true, true),
            Attempt("decoy", "correct", bounds, true, true)
        };

        var metrics = AccuracyBenchmark.Calculate("medium", attempts);

        metrics.TargetSelectionAccuracy.Should().Be(.5);
        metrics.CorrectElementActivationRate.Should().Be(.5);
        metrics.FalseSuccessRate.Should().Be(.5);
    }

    private static CandidateScore Score(TargetIntent intent, TargetCandidate candidate) =>
        TargetScorer.Score(intent, candidate);

    private static TargetCandidate Candidate(string id, string name) =>
        new(TargetMethod.WindowsUia, id, "button", name, 10, 42,
            new PhysicalRect(10, 10, 100, 40), .96, null, "invoke",
            Array.Empty<string>(), Now, CandidateId: id, CaptureId: "capture",
            ControlType: "button", SupportedActions: new[] { "invoke" });

    private static CaptureContext Capture() =>
        new(Guid.NewGuid().ToString("N"), Now, CaptureType.Window,
            42, 10, "Test", "monitor", CoordinateSpace.WindowImagePixels,
            800, 600, new PhysicalRect(0, 0, 800, 600),
            1, 1, 1, 1);

    private static AccuracyAttempt Attempt(
        string selected, string groundTruth, PhysicalRect bounds,
        bool completed, bool effect) =>
        new(Guid.NewGuid().ToString(), "medium", "Send", "capture", 10, 42,
            Array.Empty<TargetCandidate>(), selected, groundTruth, bounds, bounds,
            50, 20, selected, selected, "off", "on", true, effect, completed,
            0, 10, null, TargetMethod.WindowsUia, 1, 1, 20);

    private sealed class FakeTargetRuntime : ITargetAcquisitionRuntime
    {
        private readonly bool _completed;
        public int ResolveCalls;
        public int ActivateCalls;

        public FakeTargetRuntime(bool completed) => _completed = completed;

        public Task<TargetAcquisitionResult> ResolveAsync(
            CaptureContext capture, TargetIntent intent, VisionTarget? visionTarget,
            CancellationToken ct)
        {
            ResolveCalls++;
            var candidate = Candidate("send", "Send") with
            {
                CaptureId = capture.CaptureId,
                WindowHandle = capture.WindowHandle!.Value,
                ProcessId = capture.ProcessId!.Value
            };
            return Task.FromResult(new TargetAcquisitionResult(
                new TargetResolution(true, candidate, null, null, false),
                new[] { candidate },
                new[] { TargetScorer.Score(intent, candidate) },
                new[] { "fake-runtime" }));
        }

        public Task<TargetActivationResult> ActivateAsync(
            CaptureContext capture, TargetIntent intent, TargetCandidate target,
            CancellationToken ct)
        {
            ActivateCalls++;
            return Task.FromResult(new TargetActivationResult(
                true, true, true, true, _completed, _completed, _completed,
                "off", _completed ? "on" : "off",
                _completed ? null : "TargetActivationNotVerified",
                _completed ? null : "wrong element", new[] { "fake-activation" }));
        }

        public Task<bool> ValidatePointAsync(
            CaptureContext capture, TargetIntent intent, TargetCandidate target,
            int x, int y, CancellationToken ct) => Task.FromResult(true);

        public int PointActivateCalls;

        public Task<TargetActivationResult> TryActivateAtPointAsync(
            CaptureContext capture, int x, int y, CancellationToken ct)
        {
            PointActivateCalls++;
            return Task.FromResult(new TargetActivationResult(
                false, false, false, false, false, false, false,
                null, null, "NoPatternAtPoint", "fake: no pattern at point",
                Array.Empty<string>()));
        }
    }
}
