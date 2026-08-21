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
    [InlineData(1, 16)]     // one line
    [InlineData(3, 48)]     // Windows default
    [InlineData(5, 80)]     // this machine
    public void NotchDistance_FollowsWheelScrollLines(int lines, double expected) =>
        AnimatedScroller.NotchDistanceForLines(lines, 300).Should().Be(expected);

    [Fact]
    public void NotchDistance_MinusOneMeansOneScreen() =>
        AnimatedScroller.NotchDistanceForLines(-1, 300).Should().Be(300,
            "Windows' -1 is 'one screen at a time'");

    [Fact]
    public void NotchDistance_MinusOneWithNoViewport_FallsBackToLines() =>
        AnimatedScroller.NotchDistanceForLines(-1, 0).Should().Be(48);

    [Fact]
    public void NotchDistance_ZeroIsNonsense_AndFallsBack() =>
        AnimatedScroller.NotchDistanceForLines(0, 300).Should().Be(48,
            "scrolling zero pixels per notch would look like a dead wheel");

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

    [Fact]
    public void ReducedMotion_DoesNotAnimateAtAll()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = false;
            try
            {
                var scroll = StandaloneScroller();
                AnimatedScroller.TryWheelScroll(scroll, -120).Should().BeFalse(
                    "reduced motion means native behaviour, not a shorter animation");
                double.IsNaN(AnimatedScroller.PendingWheelTarget(scroll)).Should().BeTrue();
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
