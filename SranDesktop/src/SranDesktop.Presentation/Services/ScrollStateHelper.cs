using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Animation;

namespace SranDesktop.Presentation.Services;

/// <summary>Scroll-position logic for the chat view (Phase C PART 2).</summary>
public static class ScrollStateHelper
{
    /// <summary>Threshold (DIPs) within which we consider the view "at the bottom".</summary>
    public const double BottomThreshold = 4.0;

    /// <summary>
    /// The floating "jump to latest" button shows ONLY when the user has scrolled
    /// away from the bottom AND there is content below to scroll to. Pure so the
    /// states are unit-testable.
    /// </summary>
    public static bool ShouldShowJumpToLatest(bool isPinnedToBottom, bool hasContentBelow)
        => !isPinnedToBottom && hasContentBelow;
}

/// <summary>
/// Smoothly animates a <see cref="ScrollViewer"/>'s vertical offset via an
/// attached property (the built-in <c>VerticalOffset</c> isn't animatable).
/// Snaps instantly when reduced-motion is on (Phase 8 <see cref="MotionSettings"/>).
/// </summary>
public static class AnimatedScroller
{
    public static readonly DependencyProperty VerticalOffsetProperty =
        DependencyProperty.RegisterAttached(
            "VerticalOffset", typeof(double), typeof(AnimatedScroller),
            new PropertyMetadata(0.0, OnVerticalOffsetChanged));

    public static void SetVerticalOffset(DependencyObject o, double value) =>
        o.SetValue(VerticalOffsetProperty, value);

    public static double GetVerticalOffset(DependencyObject o) =>
        (double)o.GetValue(VerticalOffsetProperty);

    private static void OnVerticalOffsetChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is ScrollViewer sv)
            sv.ScrollToVerticalOffset((double)e.NewValue);
    }

    /// <summary>Scroll to the bottom — animated when motion is enabled, else snap.</summary>
    public static void ScrollToBottom(ScrollViewer scroll)
    {
        if (!MotionSettings.Enabled)
        {
            scroll.BeginAnimation(VerticalOffsetProperty, null); // cancel any running anim
            scroll.ScrollToEnd();
            return;
        }

        var anim = new DoubleAnimation(scroll.VerticalOffset, scroll.ScrollableHeight,
            new Duration(System.TimeSpan.FromMilliseconds(250)))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
        };
        scroll.BeginAnimation(VerticalOffsetProperty, anim);
    }
}
