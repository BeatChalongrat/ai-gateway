using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// P3 — send-time turn anchoring, decision math. ChatView feeds these with
/// real pixels; the predicates themselves are headless. Mirrors the web's
/// lib/turnAnchor.ts contract exactly.
/// </summary>
public class TurnAnchorMathTests
{
    private const double Peek = 64;
    private const double Viewport = 600;

    // ── AnchorTarget ────────────────────────────────────────────────────────

    [Fact]
    public void Target_PutsUserTopPeekBelowViewportTop_WhenPrevIsTall()
    {
        // prev spans 800..2000, user at 2000 → 2000-64: the peek shows the
        // previous message's tail.
        TurnAnchorMath.AnchorTarget(userTop: 2000, prevTop: 800, Peek)
            .Should().Be(2000 - Peek);
    }

    [Fact]
    public void Target_ClampsToPreviousTop_WhenPrevShorterThanPeek()
    {
        // prev spans 1960..2000 (40px < peek) → show all of it, never
        // overscroll past its top.
        TurnAnchorMath.AnchorTarget(userTop: 2000, prevTop: 1960, Peek)
            .Should().Be(1960);
    }

    [Fact]
    public void Target_LandsFlushAtTop_WithNoPreviousMessage()
    {
        TurnAnchorMath.AnchorTarget(userTop: 120, prevTop: null, Peek)
            .Should().Be(120);
    }

    [Fact]
    public void Target_NeverNegative()
    {
        TurnAnchorMath.AnchorTarget(userTop: 30, prevTop: 0, Peek)
            .Should().Be(0);
    }

    // ── SpacerNeeded ────────────────────────────────────────────────────────

    [Fact]
    public void Spacer_FillsTheViewportBelowShortContent()
    {
        // Anchored at 1936, viewport 600, content ends at 2100 →
        // 1936+600−2100 = 436px of spacer makes the anchor reachable.
        TurnAnchorMath.SpacerNeeded(target: 1936, Viewport, contentHeight: 2100)
            .Should().Be(436);
    }

    [Fact]
    public void Spacer_ShrinksTowardZero_AsTheReplyGrows()
    {
        var a = TurnAnchorMath.SpacerNeeded(1936, Viewport, contentHeight: 2100);
        var b = TurnAnchorMath.SpacerNeeded(1936, Viewport, contentHeight: 2300);
        var c = TurnAnchorMath.SpacerNeeded(1936, Viewport, contentHeight: 2536);
        b.Should().BeLessThan(a);
        c.Should().Be(0, "content reached the viewport bottom — no spacer left");
    }

    [Fact]
    public void Spacer_IsZero_WhenContentAlreadyExceedsTheViewport()
    {
        TurnAnchorMath.SpacerNeeded(target: 1000, Viewport, contentHeight: 5000)
            .Should().Be(0);
    }

    // ── Mid-stream / end-of-turn spacer contract (the "stuck gap" bug) ─────

    [Fact]
    public void MidStream_SpacerIsDerivedFreshAndOnlyShrinks_AsContentGrows()
    {
        // Anchored at 394 with viewport 747 (real geometry from the failing
        // trace). The spacer must be a pure function of current geometry:
        // re-deriving it for every content height gives a non-increasing
        // sequence that ends at zero — it can never stick or accumulate.
        const double offset = 394;
        const double viewport = 747;
        double? previous = null;
        foreach (var contentH in new[] { 763.0, 900, 1000, 1141, 1300 })
        {
            var spacer = TurnAnchorMath.SpacerNeeded(offset, viewport, contentH);
            if (previous is { } p)
                spacer.Should().BeLessThanOrEqualTo(p);
            previous = spacer;
        }
        previous.Should().Be(0, "content passed offset+viewport (1141) — nothing left to reserve");
    }

    [Fact]
    public void AtStreamEnd_SpacerIsZero_WhenTheReplyFilledTheViewport()
    {
        // The end-trim recomputes from geometry: a long completed answer
        // (content ≥ offset+viewport) must leave NO gap.
        TurnAnchorMath.SpacerNeeded(target: 394, viewportHeight: 747, contentHeight: 2000)
            .Should().Be(0);
    }

    // ── ShouldResumeFollow (phase handover) ─────────────────────────────────

    [Fact]
    public void Hold_WhileThereIsStillEmptyRoomBelow()
    {
        TurnAnchorMath.ShouldResumeFollow(contentHeight: 2100, offset: 1936, Viewport)
            .Should().BeFalse();
    }

    [Fact]
    public void Resume_OnceContentReachesTheViewportBottom()
    {
        TurnAnchorMath.ShouldResumeFollow(contentHeight: 2536, offset: 1936, Viewport)
            .Should().BeTrue();
    }

    // ── IsExtentReady (offset-coercion guard — the P3-desktop bug) ──────────

    [Fact]
    public void ExtentNotReady_WhenTheSpacerHasNotEnteredLayoutYet()
    {
        // The original failure: extent still 2100 while the anchor needs
        // 1936+600 — a ScrollToVerticalOffset(1936) would be coerced short.
        TurnAnchorMath.IsExtentReady(extentHeight: 2100, target: 1936, Viewport)
            .Should().BeFalse();
    }

    [Fact]
    public void ExtentReady_OnceTheSpacerIsMeasuredIn()
    {
        TurnAnchorMath.IsExtentReady(extentHeight: 2536, target: 1936, Viewport)
            .Should().BeTrue();
    }

    [Fact]
    public void ExtentReady_ForATargetDeepInsideALongThread()
    {
        TurnAnchorMath.IsExtentReady(extentHeight: 10000, target: 5000, Viewport)
            .Should().BeTrue();
    }

    // ── IsRecentTick (programmatic-vs-user ScrollChanged) ───────────────────

    [Fact]
    public void ScrollInsideTheGraceWindow_IsOurs()
    {
        TurnAnchorMath.IsRecentTick(nowTick: 1_000_100, issuedTick: 1_000_000, graceMs: 150)
            .Should().BeTrue();
    }

    [Fact]
    public void ScrollAfterTheGraceWindow_IsTheUsers()
    {
        TurnAnchorMath.IsRecentTick(nowTick: 1_000_200, issuedTick: 1_000_000, graceMs: 150)
            .Should().BeFalse();
    }

    [Fact]
    public void NoIssuedScroll_IsAlwaysTheUsers()
    {
        TurnAnchorMath.IsRecentTick(nowTick: 42, issuedTick: 0, graceMs: 150)
            .Should().BeFalse();
    }
}
