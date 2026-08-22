using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using FluentAssertions;
using SranDesktop.Presentation.Services;
using SranDesktop.Presentation.Views;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// End-to-end wheel routing through a real ListBox, because the composition is
/// what can break, not the pieces.
///
/// This file also records WHY the smooth-wheel handler is a Preview handler.
/// The safe-looking choice — bubbling MouseWheel on the ListBox — was measured
/// and cannot work: the ListBox's template ScrollViewer is a DESCENDANT, so it
/// handles the wheel and scrolls natively before the event ever reaches the
/// ListBox. <see cref="BubblingHandlerOnTheListBox_NeverSeesAnUnhandledWheel"/>
/// pins that, so the next person to reach for the obvious answer sees why it
/// was not taken.
/// </summary>
[Collection("MotionSettings")] // MotionSettings.OverrideEnabled is static — must not race
public class WheelRoutingTests
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

    private static void Pump()
    {
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.BeginInvoke(
            new Action(() => frame.Continue = false), DispatcherPriority.Loaded);
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

    private sealed class Harness : IDisposable
    {
        public ListBox List = null!;
        public ScrollViewer Outer = null!;
        public ScrollViewer Inner = null!;
        /// <summary>A markdown table wrapper: MarkdownRenderer.cs:606-611 exactly
        /// — horizontal Auto, vertical Disabled.</summary>
        public ScrollViewer Table = null!;
        public FrameworkElement PlainRow = null!;
        /// <summary>Keyboard.Modifiers cannot be forced in a unit test, so the
        /// harness carries the modifier the real handler reads.</summary>
        public bool ShiftHeld;
        public List<FrameworkElement> Items = null!;
        private Window _window = null!;

        public static Harness Build(bool attachSmoothWheel)
        {
            var h = new Harness();
            h.Items = new List<FrameworkElement>();
            for (var i = 0; i < 20; i++)
            {
                if (i == 5)
                {
                    var content = new StackPanel();
                    for (var j = 0; j < 30; j++)
                        content.Children.Add(new TextBlock { Text = $"inner {j}", Height = 20 });
                    h.Inner = new ScrollViewer
                    {
                        MaxHeight = 160,
                        VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                        Content = content,
                    };
                    h.Items.Add(new Border { Child = h.Inner, Height = 160 });
                }
                else if (i == 8)
                {
                    h.Table = new ScrollViewer
                    {
                        HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                        VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                        Content = new Border { Width = 1400, Height = 200 },
                    };
                    h.Items.Add(new Border { Child = h.Table, Height = 200 });
                }
                else
                {
                    h.Items.Add(new Border { Height = 200, Child = new TextBlock { Text = $"row {i}" } });
                }
            }
            h.PlainRow = h.Items[0];

            h.List = new ListBox { ItemsSource = h.Items, Width = 400, Height = 300 };
            // ChatView.xaml:46 — without this the item panel is measured with
            // infinite width, every row grows to its content, and a table
            // wrapper never overflows.
            ScrollViewer.SetHorizontalScrollBarVisibility(h.List, ScrollBarVisibility.Disabled);
            VirtualizingPanel.SetIsVirtualizing(h.List, true);
            VirtualizingPanel.SetVirtualizationMode(h.List, VirtualizationMode.Recycling);
            VirtualizingPanel.SetScrollUnit(h.List, ScrollUnit.Pixel);

            h._window = new Window
            {
                Content = h.List, Width = 460, Height = 360,
                Left = -32000, Top = -32000, ShowActivated = false,
                ShowInTaskbar = false, WindowStyle = WindowStyle.None,
            };
            h._window.Show();
            h.List.UpdateLayout();
            Pump();
            h.Outer = Find<ScrollViewer>(h.List)!;

            if (attachSmoothWheel)
            {
                // The same composition ChatView.OnSmoothWheel performs.
                h.List.PreviewMouseWheel += (_, e) =>
                {
                    if (e.Handled) return;
                    if (h.ShiftHeld
                        && ChatView.TryPanHorizontally(
                            e.OriginalSource as DependencyObject, h.Outer, -e.Delta))
                    {
                        e.Handled = true;
                        return;
                    }
                    if (ChatView.InnerScrollerWantsWheel(e.OriginalSource as DependencyObject, h.Outer, e.Delta)) return;
                    if (!AnimatedScroller.TryWheelScroll(h.Outer, e.Delta)) return;
                    e.Handled = true;
                };
            }
            return h;
        }

        /// <summary>Raise the wheel the way the input system would: tunnel from
        /// the root down to <paramref name="over"/>, then bubble back up.</summary>
        public void WheelOver(FrameworkElement over, int delta)
        {
            var preview = new MouseWheelEventArgs(Mouse.PrimaryDevice, Environment.TickCount, delta)
            {
                RoutedEvent = UIElement.PreviewMouseWheelEvent,
                Source = over,
            };
            over.RaiseEvent(preview);
            if (preview.Handled)
                return;
            var bubble = new MouseWheelEventArgs(Mouse.PrimaryDevice, Environment.TickCount, delta)
            {
                RoutedEvent = UIElement.MouseWheelEvent,
                Source = over,
            };
            over.RaiseEvent(bubble);
        }

        public void Realize(int index)
        {
            List.ScrollIntoView(Items[index]);
            List.UpdateLayout();
            Pump();
        }

        public void Dispose() => _window.Close();
    }

    /// <summary>
    /// The measurement that ruled out the bubbling attachment point. The outer
    /// ScrollViewer has already handled — and already scrolled — by the time
    /// the event reaches the ListBox.
    /// </summary>
    [Fact]
    public void BubblingHandlerOnTheListBox_NeverSeesAnUnhandledWheel()
    {
        RunSta(() =>
        {
            using var h = Harness.Build(attachSmoothWheel: false);
            var unhandledFired = false;
            var handledTooSawHandled = (bool?)null;

            h.List.AddHandler(UIElement.MouseWheelEvent,
                new MouseWheelEventHandler((_, _) => unhandledFired = true), false);
            h.List.AddHandler(UIElement.MouseWheelEvent,
                new MouseWheelEventHandler((_, e) => handledTooSawHandled = e.Handled), true);

            var before = h.Outer.VerticalOffset;
            h.WheelOver(h.PlainRow, -120);
            h.List.UpdateLayout();
            Pump();

            unhandledFired.Should().BeFalse(
                "the ListBox's own ScrollViewer is a descendant and handles the wheel first");
            handledTooSawHandled.Should().BeTrue();
            h.Outer.VerticalOffset.Should().BeGreaterThan(before,
                "and it had already scrolled natively — too late to animate");
        });
    }

    [Fact]
    public void OverAPlainRow_TheWheelIsAnimatedInsteadOfJumping()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                using var h = Harness.Build(attachSmoothWheel: true);
                var before = h.Outer.VerticalOffset;
                var perNotch = AnimatedScroller.NotchDistanceForLines(
                    SystemParameters.WheelScrollLines, h.Outer.ViewportHeight);

                h.WheelOver(h.PlainRow, -120);

                AnimatedScroller.PendingWheelTarget(h.Outer)
                    .Should().BeApproximately(before + perNotch, 0.5,
                        "the notch is aimed at a target rather than applied at once");
                (h.Outer.VerticalOffset - before).Should().BeLessThan(perNotch,
                    "and the view has NOT already jumped the whole distance");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    [Fact]
    public void OverAnInnerScroller_TheInnerPanelStillScrolls_AndTheOuterDoesNot()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                using var h = Harness.Build(attachSmoothWheel: true);
                h.Realize(5);
                h.Inner.ScrollableHeight.Should().BeGreaterThan(0, "the inner viewer must be live");

                var innerBefore = h.Inner.VerticalOffset;
                var outerBefore = h.Outer.VerticalOffset;

                h.WheelOver(h.Inner, -120);
                h.List.UpdateLayout();
                Pump();

                h.Inner.VerticalOffset.Should().BeGreaterThan(innerBefore,
                    "the five capped viewers in a message bubble must keep the wheel");
                h.Outer.VerticalOffset.Should().Be(outerBefore, "and the thread must not move with it");
                double.IsNaN(AnimatedScroller.PendingWheelTarget(h.Outer)).Should().BeTrue(
                    "no animation should have been armed at all");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>
    /// THE FIX. An inner viewer that has run out of room hands the wheel
    /// outward instead of swallowing it, which is what a browser does.
    /// Measured before the change: panel pinned at 319/319, thread frozen at
    /// 1200, further notches moved nothing.
    /// </summary>
    [Fact]
    public void InnerScrollerAtItsLimit_ChainsOutwardToTheThread()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                using var h = Harness.Build(attachSmoothWheel: true);
                h.Realize(5);
                h.Inner.ScrollToEnd();
                h.List.UpdateLayout();
                Pump();
                h.Inner.VerticalOffset.Should().BeApproximately(h.Inner.ScrollableHeight, 0.5,
                    "the panel must actually be at its bottom for this to mean anything");

                var innerBefore = h.Inner.VerticalOffset;
                var outerBefore = h.Outer.VerticalOffset;
                var perNotch = AnimatedScroller.NotchDistanceForLines(
                    SystemParameters.WheelScrollLines, h.Outer.ViewportHeight);

                h.WheelOver(h.Inner, -120);
                h.List.UpdateLayout();
                Pump();

                h.Inner.VerticalOffset.Should().BeApproximately(innerBefore, 0.5,
                    "the panel has nowhere left to go downwards");
                AnimatedScroller.PendingWheelTarget(h.Outer)
                    .Should().BeApproximately(outerBefore + perNotch, 0.5,
                        "so the thread takes the notch instead of the wheel dying");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>
    /// "At its limit" is DIRECTIONAL. A panel scrolled to the bottom must still
    /// take an upward wheel, or chaining would make the five capped viewers
    /// unscrollable back up, which is a worse bug than the one being fixed.
    /// </summary>
    [Fact]
    public void InnerScrollerAtItsBottom_StillTakesAnUpwardWheel()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                using var h = Harness.Build(attachSmoothWheel: true);
                h.Realize(5);
                h.Inner.ScrollToEnd();
                h.List.UpdateLayout();
                Pump();

                var innerBefore = h.Inner.VerticalOffset;
                var outerBefore = h.Outer.VerticalOffset;

                h.WheelOver(h.Inner, +120); // upward
                h.List.UpdateLayout();
                Pump();

                h.Inner.VerticalOffset.Should().BeLessThan(innerBefore,
                    "the panel still has room in THIS direction");
                h.Outer.VerticalOffset.Should().Be(outerBefore, "and the thread must not move with it");
                double.IsNaN(AnimatedScroller.PendingWheelTarget(h.Outer)).Should().BeTrue();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>
    /// Pins the assumption that let the old vertical-Disabled clause be deleted
    /// rather than kept beside the directional check: a vertically disabled
    /// viewer reports no scrollable height even when its content is far taller
    /// than its viewport, so the directional check already covers it.
    /// </summary>
    [Fact]
    public void VerticallyDisabledScroller_ReportsNoScrollableHeight_EvenWithTallerContent()
    {
        RunSta(() =>
        {
            var inner = new ScrollViewer
            {
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                Content = new Border { Width = 900, Height = 4000 },
            };
            inner.Measure(new Size(200, 100));
            inner.Arrange(new Rect(0, 0, 200, 100));
            inner.UpdateLayout();

            inner.ScrollableHeight.Should().Be(0,
                "so the directional predicate alone is enough to let it through");
            ChatView.InnerScrollerWantsWheel(inner, new ScrollViewer(), -120).Should().BeFalse();
            ChatView.InnerScrollerWantsWheel(inner, new ScrollViewer(), +120).Should().BeFalse();
        });
    }

    // -- horizontal ---------------------------------------------------------

    /// <summary>shift+wheel pans a table, and leaves the thread alone.</summary>
    [Fact]
    public void ShiftWheelOverATable_PansIt_AndDoesNotMoveTheThread()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                using var h = Harness.Build(attachSmoothWheel: true);
                h.Realize(8);
                h.Table.ScrollableWidth.Should().BeGreaterThan(0, "the table must actually overflow");
                h.ShiftHeld = true;

                var outerBefore = h.Outer.VerticalOffset;
                h.WheelOver(h.Table, -120);
                h.List.UpdateLayout();
                Pump();

                h.Table.HorizontalOffset.Should().BeGreaterThan(0, "shift+wheel-down pans right");
                h.Outer.VerticalOffset.Should().Be(outerBefore);
                double.IsNaN(AnimatedScroller.PendingWheelTarget(h.Outer)).Should().BeTrue(
                    "the thread must not have been armed at all");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>At the table right edge the gesture STOPS. Browsers chain
    /// outward in the same axis and never cross into the other one; nothing
    /// above a table scrolls horizontally, so the correct answer is nothing,
    /// not a surprise jump down the thread.</summary>
    [Fact]
    public void ShiftWheelAtTheTablesRightEdge_StopsInsteadOfScrollingTheThread()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                using var h = Harness.Build(attachSmoothWheel: true);
                h.Realize(8);
                h.Table.ScrollToRightEnd();
                h.List.UpdateLayout();
                Pump();
                h.ShiftHeld = true;

                var outerBefore = h.Outer.VerticalOffset;
                var tableBefore = h.Table.HorizontalOffset;

                h.WheelOver(h.Table, -120);
                h.List.UpdateLayout();
                Pump();

                h.Table.HorizontalOffset.Should().BeApproximately(tableBefore, 0.5);
                h.Outer.VerticalOffset.Should().Be(outerBefore, "no axis crossing");
                double.IsNaN(AnimatedScroller.PendingWheelTarget(h.Outer)).Should().BeTrue();
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>shift+wheel over ordinary prose is left exactly as it is today.
    /// Claiming it everywhere would trade one dead input for another.</summary>
    [Fact]
    public void ShiftWheelOverPlainProse_StillScrollsTheThread()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = true;
            try
            {
                using var h = Harness.Build(attachSmoothWheel: true);
                h.ShiftHeld = true;
                var before = h.Outer.VerticalOffset;
                var perNotch = AnimatedScroller.NotchDistanceForLines(
                    SystemParameters.WheelScrollLines, h.Outer.ViewportHeight);

                h.WheelOver(h.PlainRow, -120);

                AnimatedScroller.PendingWheelTarget(h.Outer)
                    .Should().BeApproximately(before + perNotch, 0.5);
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }

    /// <summary>The walk skips a container that has hit its limit and keeps
    /// looking outward, the same chaining the vertical predicate does.</summary>
    [Fact]
    public void HorizontalScrollTarget_ChainsPastAContainerAtItsLimit()
    {
        RunSta(() =>
        {
            var stopAt = new ScrollViewer();
            var innerContent = new Border { Width = 900, Height = 40 };
            var inner = new ScrollViewer
            {
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                Content = innerContent,
            };
            var outerHost = new Border { Width = 400, Height = 300, Child = inner };
            var outerPan = new ScrollViewer
            {
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                Content = outerHost,
            };
            outerPan.Measure(new Size(200, 300));
            outerPan.Arrange(new Rect(0, 0, 200, 300));
            outerPan.UpdateLayout();
            inner.ScrollableWidth.Should().BeGreaterThan(0);
            outerPan.ScrollableWidth.Should().BeGreaterThan(0);

            ChatView.HorizontalScrollTarget(innerContent, stopAt, true, out var any)
                .Should().BeSameAs(inner, "the innermost one that can move wins");
            any.Should().BeTrue();

            inner.ScrollToRightEnd();
            inner.UpdateLayout();
            ChatView.HorizontalScrollTarget(innerContent, stopAt, true, out any)
                .Should().BeSameAs(outerPan, "at its limit it chains outward");
            any.Should().BeTrue();
        });
    }

    /// <summary>
    /// THE SIGN. WM_MOUSEHWHEEL positive means tilt-RIGHT, which INCREASES the
    /// horizontal offset, the opposite convention to WM_MOUSEWHEEL where
    /// positive means away-from-user and DECREASES the vertical offset.
    /// Getting this backwards would pan tables the wrong way, which is worse
    /// than not panning them at all.
    /// </summary>
    [Fact]
    public void PanBy_PositiveDelta_MovesRight_NegativeMovesLeft()
    {
        RunSta(() =>
        {
            var pan = new ScrollViewer
            {
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                Content = new Border { Width = 2000, Height = 40 },
            };
            pan.Measure(new Size(200, 40));
            pan.Arrange(new Rect(0, 0, 200, 40));
            pan.UpdateLayout();

            ChatView.PanBy(pan, +120);
            pan.UpdateLayout();
            pan.HorizontalOffset.Should().BeGreaterThan(0, "tilt-right scrolls right");

            var afterRight = pan.HorizontalOffset;
            ChatView.PanBy(pan, -120);
            pan.UpdateLayout();
            pan.HorizontalOffset.Should().BeLessThan(afterRight, "and tilt-left comes back");
        });
    }

    /// <summary>A precision touchpad sends fractional notches; the reach must
    /// scale with them rather than snapping to a whole notch.</summary>
    [Fact]
    public void PanBy_ScalesWithAFractionalNotch()
    {
        RunSta(() =>
        {
            static ScrollViewer Fresh()
            {
                var sv = new ScrollViewer
                {
                    HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                    VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                    Content = new Border { Width = 2000, Height = 40 },
                };
                sv.Measure(new Size(200, 40));
                sv.Arrange(new Rect(0, 0, 200, 40));
                sv.UpdateLayout();
                return sv;
            }

            var full = Fresh();
            ChatView.PanBy(full, 120);
            full.UpdateLayout();

            var third = Fresh();
            ChatView.PanBy(third, 40);
            third.UpdateLayout();

            third.HorizontalOffset.Should().BeApproximately(full.HorizontalOffset / 3, 0.5);
        });
    }

    // ── the guard itself ───────────────────────────────────────────────────

    [Fact]
    public void InnerScrollViewerWithRoomLeft_KeepsTheWheel()
    {
        RunSta(() =>
        {
            var outer = new ScrollViewer();
            var inner = new ScrollViewer
            {
                MaxHeight = 160,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Content = new Border { Height = 600 },
            };
            inner.Measure(new Size(400, 160));
            inner.Arrange(new Rect(0, 0, 400, 160));
            inner.UpdateLayout();

            inner.ScrollableHeight.Should().BeGreaterThan(0);
            ChatView.InnerScrollerWantsWheel(inner, outer, -120).Should().BeTrue(
                "the five capped viewers in a message bubble must keep scrolling");
        });
    }

    /// <summary>An inner viewer that cannot scroll VERTICALLY must not block
    /// the thread — the renderer's code-block and table wrappers are
    /// horizontal-only (MarkdownRenderer.cs:244, :606).</summary>
    [Fact]
    public void HorizontalOnlyInnerScroller_DoesNotBlockTheWheel()
    {
        RunSta(() =>
        {
            var outer = new ScrollViewer();
            var inner = new ScrollViewer
            {
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                Width = 200,
                Content = new Border { Width = 900, Height = 40 },
            };
            inner.Measure(new Size(200, 40));
            inner.Arrange(new Rect(0, 0, 200, 40));
            inner.UpdateLayout();

            ChatView.InnerScrollerWantsWheel(inner, outer, -120).Should().BeFalse();
        });
    }

    [Fact]
    public void OuterItself_IsNotTreatedAsAnInnerScroller()
    {
        RunSta(() =>
        {
            var outer = new ScrollViewer();
            ChatView.InnerScrollerWantsWheel(outer, outer, -120).Should().BeFalse(
                "the walk stops at the message list's own viewer");
        });
    }

    [Fact]
    public void NullSource_IsSafe()
    {
        RunSta(() =>
        {
            ChatView.InnerScrollerWantsWheel(null, new ScrollViewer(), -120).Should().BeFalse();
        });
    }

    /// <summary>Reduced motion removes the animation, not the reach: the notch
    /// still travels the full Chromium distance, it just arrives in one
    /// frame.</summary>
    [Fact]
    public void ReducedMotion_ScrollsTheFullDistanceInOneFrame()
    {
        RunSta(() =>
        {
            MotionSettings.OverrideEnabled = false;
            try
            {
                using var h = Harness.Build(attachSmoothWheel: true);
                var before = h.Outer.VerticalOffset;
                var perNotch = AnimatedScroller.NotchDistanceForLines(
                    SystemParameters.WheelScrollLines, h.Outer.ViewportHeight);

                h.WheelOver(h.PlainRow, -120);
                h.List.UpdateLayout();
                Pump();

                (h.Outer.VerticalOffset - before).Should().BeApproximately(perNotch, 1.0,
                    "the whole distance arrives at once");
                double.IsNaN(AnimatedScroller.PendingWheelTarget(h.Outer)).Should().BeTrue(
                    "with nothing left in flight");
            }
            finally { MotionSettings.OverrideEnabled = null; }
        });
    }
}
