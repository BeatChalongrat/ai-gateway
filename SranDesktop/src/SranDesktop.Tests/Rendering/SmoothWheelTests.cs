using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using FluentAssertions;
using SranDesktop.Presentation.Services;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Smooth wheel scrolling. Two things are being protected here, and they pull
/// in opposite directions: a wheel notch must be ANIMATED rather than applied
/// in one frame, and the five capped ScrollViewers inside a message bubble
/// must keep taking the wheel exactly as they do today.
/// </summary>
[Collection("MotionSettings")] // MotionSettings.OverrideEnabled is static — must not race
public class SmoothWheelTests
{
    private static void RunSta(Action action)
    {
        Exception? failure = null;
        var t = new Thread(() =>
        {
            try { action(); }
            catch (Exception ex) { failure = ex; }
            finally { Dispatcher.CurrentDispatcher.InvokeShutdown(); }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        if (failure is not null) throw failure;
    }

    private static void Pump(DispatcherPriority through = DispatcherPriority.Loaded)
    {
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(() => frame.Continue = false), through);
        Dispatcher.PushFrame(frame);
    }

    private static T? Find<T>(DependencyObject root) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is T match) return match;
            if (Find<T>(child) is { } nested) return nested;
        }
        return null;
    }

    private static MouseWheelEventArgs Wheel(int delta) =>
        new(Mouse.PrimaryDevice, Environment.TickCount, delta) { RoutedEvent = UIElement.MouseWheelEvent };

    /// <summary>A ScrollViewer with real geometry, without needing a window.</summary>
    private static ScrollViewer StandaloneScroller(double viewport = 300, double content = 3000)
    {
        var scroll = new ScrollViewer
        {
            Height = viewport,
            Width = 400,
            Content = new Border { Height = content },
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        };
        scroll.Measure(new Size(400, viewport));
        scroll.Arrange(new Rect(0, 0, 400, viewport));
        scroll.UpdateLayout();
        return scroll;
    }

    // ── distance mapping ───────────────────────────────────────────────────

    [Theory]
    [InlineData(1, 40)]     // one line
    [InlineData(3, 120)]    // Windows default — what Chromium travels
    [InlineData(5, 200)]    // this machine
    public void NotchDistance_FollowsWheelScrollLines(int lines, double expected) =>
        AnimatedScroller.NotchDistanceForLines(lines, 300).Should().Be(expected);

    [Fact]
    public void NotchDistance_MinusOneMeansOneScreen() =>
        AnimatedScroller.NotchDistanceForLines(-1, 300).Should().Be(300,
            "Windows' -1 is 'one screen at a time'");

    [Fact]
    public void NotchDistance_MinusOneWithNoViewport_FallsBackToLines() =>
        AnimatedScroller.NotchDistanceForLines(-1, 0).Should().Be(120);

    [Fact]
    public void NotchDistance_ZeroIsNonsense_AndFallsBack() =>
        AnimatedScroller.NotchDistanceForLines(0, 300).Should().Be(120,
            "scrolling zero pixels per notch would look like a dead wheel");

    // ── duration: inverse-delta ────────────────────────────────────────────

    /// <summary>Chromium's kInverseDelta ramp, verified against the source
    /// constants in cc/animation/scroll_offset_animation_curve.cc. Note the
    /// direction: the FARTHER it travels, the SHORTER the animation.</summary>
    [Theory]
    [InlineData(120, 200)]   // one notch from rest
    [InlineData(240, 167)]
    [InlineData(360, 133)]
    [InlineData(480, 100)]   // rapid wheeling
    public void Duration_FollowsTheInverseDeltaRamp(double pendingPx, double expectedMs) =>
        (AnimatedScroller.WheelDurationSeconds(pendingPx) * 1000)
            .Should().BeApproximately(expectedMs, 1.0);

    [Fact]
    public void Duration_IsClampedAtBothEnds()
    {
        (AnimatedScroller.WheelDurationSeconds(10) * 1000).Should().BeApproximately(200, 1.0,
            "below the ramp start it holds at the maximum");
        (AnimatedScroller.WheelDurationSeconds(5000) * 1000).Should().BeApproximately(100, 1.0,
            "beyond the ramp end it holds at the minimum");
    }

    [Fact]
    public void Duration_IsMonotonicallyShorterWithDistance()
    {
        var previous = double.MaxValue;
        for (var d = 120.0; d <= 480; d += 30)
        {
            var current = AnimatedScroller.WheelDurationSeconds(d);
            current.Should().BeLessThanOrEqualTo(previous);
            previous = current;
        }
    }

    // ── the curve itself ───────────────────────────────────────────────────

    /// <summary>cubic-bezier(0.42, 0, 0.58, 1) is symmetric and starts from
    /// ZERO velocity — that is what gives it a sense of mass. An ease-out curve
    /// leaves at maximum velocity, which from a standing start reads as a
    /// snap.</summary>
    [Fact]
    public void EaseInOutCurve_StartsAndEndsAtZeroVelocity()
    {
        var start = CubicBezier.SlopeAtX(0.42, 0.0, 0.58, 1.0, 0.0);
        var middle = CubicBezier.SlopeAtX(0.42, 0.0, 0.58, 1.0, 0.5);
        var end = CubicBezier.SlopeAtX(0.42, 0.0, 0.58, 1.0, 1.0);

        start.Should().BeApproximately(0, 0.01);
        end.Should().BeApproximately(0, 0.01);
        middle.Should().BeGreaterThan(1.0, "peak speed is in the middle");
    }

    [Fact]
    public void SlopeScaledCurve_HasThatSlopeAtTheStart()
    {
        foreach (var slope in new[] { 0.5, 1.0, 2.0 })
        {
            CubicBezier.SlopeAtX(0.42, 0.42 * slope, 0.58, 1.0, 0.0)
                .Should().BeApproximately(slope, 0.01,
                    "y1 = x1 x slope is exactly how Chromium injects an initial velocity");
        }
    }

    [Fact]
    public void SolveForT_RoundTrips()
    {
        for (var x = 0.0; x <= 1.0; x += 0.05)
        {
            var t = CubicBezier.SolveForT(x, 0.42, 0.58);
            var back = 3 * (1 - t) * (1 - t) * t * 0.42 + 3 * (1 - t) * t * t * 0.58 + t * t * t;
            back.Should().BeApproximately(x, 1e-4);
        }
    }

    // ── retarget preserves velocity ────────────────────────────────────────

    [Fact]
    public void FirstNotchFromRest_StartsAtZeroVelocity()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                AnimatedScroller.TryWheelScroll(scroll, -120);
                AnimatedScroller.PendingWheelSlope(scroll).Should().Be(0);
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>
    /// A notch arriving mid-flight must build its curve with the speed already
    /// on the clock. Without it every notch restarts from a standstill and
    /// rapid wheeling stutters once per notch — which would look exactly like
    /// the jank 1.2.7 removed, from an unrelated cause.
    /// </summary>
    [Fact]
    public void RetargetMidFlight_CarriesTheCurrentVelocity()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                AnimatedScroller.TryWheelScroll(scroll, -120);

                // Let the curve get up to speed (peak velocity is mid-flight).
                var deadline = Environment.TickCount64 + 90;
                while (Environment.TickCount64 < deadline)
                {
                    Pump(DispatcherPriority.SystemIdle);
                    Thread.Sleep(5);
                }

                AnimatedScroller.TryWheelScroll(scroll, -120).Should().BeTrue();
                AnimatedScroller.PendingWheelSlope(scroll).Should().BeGreaterThan(0,
                    "the new curve must start moving, not from a standstill");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>
    /// WPF's KeySpline requires both control-point coordinates within [0,1], so
    /// an initial slope above 1/0.42 = 2.38 cannot be expressed exactly and
    /// saturates. This records the slopes a realistic notch burst actually
    /// produces, so the report can say whether that limit ever binds.
    /// </summary>
    [Fact]
    public void RetargetSlopes_InARealisticBurst_AreRecorded()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller(viewport: 600, content: 40000);
                var slopes = new List<double>();

                for (var i = 0; i < 8; i++)
                {
                    AnimatedScroller.TryWheelScroll(scroll, -120);
                    slopes.Add(AnimatedScroller.PendingWheelSlope(scroll));
                    // ~60 ms between notches is brisk but human wheeling.
                    var until = Environment.TickCount64 + 60;
                    while (Environment.TickCount64 < until)
                    {
                        Pump(DispatcherPriority.SystemIdle);
                        Thread.Sleep(5);
                    }
                }

                var max = slopes.Max();
                var clampLimit = 1.0 / 0.42;
                TestOutput.Record(
                    "[wheel] retarget slopes over 8 notches at ~60ms: " +
                    string.Join(", ", slopes.Select(s => s.ToString("F2"))) +
                    $" | max={max:F2} KeySpline limit={clampLimit:F2} " +
                    $"clamped={(max > clampLimit ? "YES" : "no")}");

                slopes.Should().NotBeEmpty();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    // ── the target accumulates ─────────────────────────────────────────────

    [Fact]
    public void SecondNotchExtendsThePendingTarget_RatherThanReAiming()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                var perNotch = AnimatedScroller.NotchDistanceForLines(
                    SystemParameters.WheelScrollLines, scroll.ViewportHeight);

                AnimatedScroller.TryWheelScroll(scroll, -120).Should().BeTrue();
                AnimatedScroller.PendingWheelTarget(scroll).Should().BeApproximately(perNotch, 0.5);

                // Second notch arrives mid-flight — no pumping, the animation is
                // still running and the visible offset has barely moved.
                AnimatedScroller.TryWheelScroll(scroll, -120).Should().BeTrue();
                AnimatedScroller.PendingWheelTarget(scroll).Should().BeApproximately(perNotch * 2, 0.5,
                    "a burst of notches must add up, not restart from the visible offset");

                AnimatedScroller.TryWheelScroll(scroll, -120);
                AnimatedScroller.PendingWheelTarget(scroll).Should().BeApproximately(perNotch * 3, 0.5);
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    [Fact]
    public void TargetIsClampedWhileAccumulating_SoTheEndOfAThreadCannotOvershoot()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                for (var i = 0; i < 200; i++)
                    AnimatedScroller.TryWheelScroll(scroll, -120);

                AnimatedScroller.PendingWheelTarget(scroll).Should().BeApproximately(scroll.ScrollableHeight, 0.5,
                    "an invisible overshoot would have to be unwound before anything moved again");

                for (var i = 0; i < 200; i++)
                    AnimatedScroller.TryWheelScroll(scroll, 120);
                AnimatedScroller.PendingWheelTarget(scroll).Should().BeApproximately(0, 0.5);
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    // ── every path that clears the target ──────────────────────────────────

    [Fact]
    public void AnchorScroll_ClearsThePendingTarget()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                AnimatedScroller.TryWheelScroll(scroll, -120);
                double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll)).Should().BeFalse();

                AnimatedScroller.ScrollTo(scroll, 500);
                double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll)).Should().BeTrue();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    [Fact]
    public void JumpToLatest_ClearsThePendingTarget()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                AnimatedScroller.TryWheelScroll(scroll, -120);
                AnimatedScroller.ScrollToBottom(scroll);
                double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll)).Should().BeTrue();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>
    /// Detaching an animation reverts the attached DP to its LOCAL value, and
    /// nothing ever sets one — so the effective value goes from wherever the
    /// animation had it to the default 0, the property-changed callback fires,
    /// and the view is yanked to the top of the thread. Cancelling must be
    /// invisible.
    /// </summary>
    [Fact]
    public void CancelWheel_DoesNotMoveTheView()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                scroll.ScrollToVerticalOffset(900);
                scroll.UpdateLayout();

                AnimatedScroller.TryWheelScroll(scroll, -120);
                // Let the animation actually take hold of the property.
                var deadline = Environment.TickCount64 + 300;
                while (Environment.TickCount64 < deadline)
                {
                    Pump(DispatcherPriority.SystemIdle);
                    Thread.Sleep(10);
                }

                var before = scroll.VerticalOffset;
                before.Should().BeGreaterThan(500, "precondition: we are mid-thread");

                AnimatedScroller.CancelWheel(scroll);
                scroll.UpdateLayout();
                Pump(DispatcherPriority.SystemIdle);

                scroll.VerticalOffset.Should().BeApproximately(before, 1.0,
                    "cancelling a wheel animation must not scroll anywhere");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    [Fact]
    public void CancelWheel_ClearsThePendingTarget()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                AnimatedScroller.TryWheelScroll(scroll, -120);
                AnimatedScroller.CancelWheel(scroll);
                double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll)).Should().BeTrue();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    [Fact]
    public void CompletedAnimation_ClearsThePendingTarget()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                AnimatedScroller.TryWheelScroll(scroll, -120);

                var deadline = Environment.TickCount64 + 2000;
                while (!double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll))
                       && Environment.TickCount64 < deadline)
                {
                    Pump(DispatcherPriority.SystemIdle);
                    Thread.Sleep(10);
                }

                double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll)).Should().BeTrue(
                    "a stale target would fling the view on the next notch");
                scroll.VerticalOffset.Should().BeGreaterThan(0, "and it actually scrolled");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    // ── reduced motion and trackpads pass straight through ─────────────────

    /// <summary>
    /// Distance is not motion. Reduced motion removes the ANIMATION, not the
    /// reach: the notch must still travel the full Chromium distance, it just
    /// arrives in one frame. Gating distance on the animation would leave
    /// reduced-motion users on WPF's short 16-px-per-line scroll forever.
    /// </summary>
    [Fact]
    public void ReducedMotion_StillTravelsTheFullDistance_JustInstantly()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = false;
            try
            {
                var scroll = StandaloneScroller();
                var perNotch = AnimatedScroller.NotchDistanceForLines(
                    SystemParameters.WheelScrollLines, scroll.ViewportHeight);

                AnimatedScroller.TryWheelScroll(scroll, -120).Should().BeTrue();
                scroll.UpdateLayout();

                scroll.VerticalOffset.Should().BeApproximately(perNotch, 0.5,
                    "the whole distance arrives at once, with no animation");
                double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll)).Should().BeTrue(
                    "and nothing is left in flight");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    [Theory]
    [InlineData(40)]    // precision touchpad, small increment
    [InlineData(-13)]   // precision touchpad, arbitrary
    [InlineData(119)]
    [InlineData(-121)]
    [InlineData(0)]
    public void TrackpadShapedDeltas_PassThroughToNativeHandling(int delta)
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller();
                AnimatedScroller.TryWheelScroll(scroll, delta).Should().BeFalse();
                double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll)).Should().BeTrue();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    [Theory]
    [InlineData(120)]
    [InlineData(-120)]
    [InlineData(240)]   // coalesced notches
    [InlineData(-360)]
    public void WholeNotches_AreAnimated(int delta)
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                // Start mid-thread so both directions have somewhere to go.
                var scroll = StandaloneScroller();
                scroll.ScrollToVerticalOffset(1000);
                scroll.UpdateLayout();

                AnimatedScroller.TryWheelScroll(scroll, delta).Should().BeTrue();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    [Fact]
    public void NothingToScroll_PassesThrough()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                var scroll = StandaloneScroller(viewport: 300, content: 100); // no overflow
                AnimatedScroller.TryWheelScroll(scroll, -120).Should().BeFalse();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

}
