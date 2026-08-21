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
    /// <summary>
    /// ── THE FEEL KNOBS ──────────────────────────────────────────────────
    /// Everything that decides how a wheel notch feels, in one place, because
    /// this is tuned by eye and nobody should have to hunt for it.
    ///
    /// Why any of this exists: WPF applies a wheel notch in a single frame —
    /// the view is at offset X, then at X+80, with nothing in between.
    /// Chromium animates every notch over roughly a tenth of a second, which
    /// is the whole reason a browser reads as smooth at the same pixel
    /// distance. This animates the notch; it does not change how far it goes.
    /// </summary>
    public static class WheelFeel
    {
        /// <summary>One notch's travel time. Deliberately much shorter than
        /// the 250 ms used for a deliberate jump to a new turn: at 250 ms a
        /// wheel notch feels like wading.</summary>
        public const int DurationMs = 120;

        /// <summary>Ease out only — a wheel notch has no run-up. Anything with
        /// an EaseIn phase reads as lag.</summary>
        public static IEasingFunction NewEasing() =>
            new CubicEase { EasingMode = EasingMode.EaseOut };

        /// <summary>Multiplier on the OS-derived distance. 1.0 = exactly what
        /// Windows was already going to scroll. Raise it if the owner wants
        /// more travel per notch; it does NOT belong above ~1.5 before the
        /// animation starts to overshoot the eye.</summary>
        public const double DistanceMultiplier = 1.0;

        /// <summary>DIP per text line, matching what WPF's own ScrollViewer
        /// uses (measured: WheelScrollLines=5 scrolled exactly 80 DIP).</summary>
        public const double LineHeightDip = 16.0;

        /// <summary>Fallback when the OS reports a nonsense line count.</summary>
        public const int FallbackLines = 3;

        /// <summary>One physical notch. Windows reports wheel movement in
        /// units of WHEEL_DELTA = 120.</summary>
        public const int WheelDelta = 120;
    }

    public static readonly DependencyProperty VerticalOffsetProperty =
        DependencyProperty.RegisterAttached(
            "VerticalOffset", typeof(double), typeof(AnimatedScroller),
            new PropertyMetadata(0.0, OnVerticalOffsetChanged));

    /// <summary>
    /// Where an in-flight wheel animation is heading, so a second notch
    /// arriving mid-flight extends that target instead of re-aiming from the
    /// offset currently on screen. Re-aiming from the visible offset is what
    /// makes fast wheeling feel like the thread barely moves.
    ///
    /// NaN = nothing pending. A STALE target is worse than none — it would
    /// fling the view somewhere the user never asked for — so every path that
    /// takes the scroller away from the wheel clears it: natural completion,
    /// an anchor scroll, a jump-to-latest, and <see cref="CancelWheel"/> for
    /// everything the view knows about (thumb drag, keyboard, conversation
    /// switch).
    /// </summary>
    private static readonly DependencyProperty WheelTargetProperty =
        DependencyProperty.RegisterAttached(
            "WheelTarget", typeof(double), typeof(AnimatedScroller),
            new PropertyMetadata(double.NaN));

    public static void SetVerticalOffset(DependencyObject o, double value) =>
        o.SetValue(VerticalOffsetProperty, value);

    public static double GetVerticalOffset(DependencyObject o) =>
        (double)o.GetValue(VerticalOffsetProperty);

    private static void OnVerticalOffsetChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is ScrollViewer sv)
            sv.ScrollToVerticalOffset((double)e.NewValue);
    }

    /// <summary>
    /// Animate one wheel gesture. Returns false when the caller should leave
    /// the event alone and let WPF scroll natively — reduced motion, a
    /// trackpad-shaped delta, or nowhere left to scroll.
    ///
    /// <paramref name="delta"/> is the raw <c>MouseWheelEventArgs.Delta</c>:
    /// positive scrolls up (offset decreases), in units of 120 per notch.
    /// </summary>
    public static bool TryWheelScroll(ScrollViewer scroll, int delta)
    {
        // Reduced motion means reduced motion, not a shorter animation. Native
        // behaviour, entirely unchanged.
        if (!MotionSettings.Enabled)
            return false;

        if (!IsMouseWheelShapedDelta(delta))
            return false;

        if (scroll.ScrollableHeight <= 0)
            return false;

        var distance = NotchDistance(scroll) * WheelFeel.DistanceMultiplier;
        var travel = -(delta / (double)WheelFeel.WheelDelta) * distance;

        // Accumulate toward the PENDING target when one is in flight, so a
        // burst of notches adds up instead of each one restarting from
        // wherever the animation happens to have got to.
        var basis = (double)scroll.GetValue(WheelTargetProperty);
        if (double.IsNaN(basis))
            basis = scroll.VerticalOffset;

        // Clamp at ACCUMULATION time, not only when animating: otherwise
        // wheeling hard at the end of a thread builds an invisible overshoot
        // the user then has to unwind before anything moves.
        var target = Math.Max(0, Math.Min(basis + travel, scroll.ScrollableHeight));

        // Already exactly where this notch would take us (top or bottom):
        // nothing to animate, and claiming the event would swallow it.
        if (Math.Abs(target - scroll.VerticalOffset) < 0.5 && double.IsNaN(basis))
            return false;

        scroll.SetValue(WheelTargetProperty, target);

        var anim = new DoubleAnimation(scroll.VerticalOffset, target,
            new Duration(System.TimeSpan.FromMilliseconds(WheelFeel.DurationMs)))
        {
            EasingFunction = WheelFeel.NewEasing(),
        };
        anim.Completed += (_, _) =>
        {
            // Only clear if this animation is still the one in charge; a newer
            // notch or an anchor scroll may have moved the target on.
            var current = (double)scroll.GetValue(WheelTargetProperty);
            if (!double.IsNaN(current) && Math.Abs(current - target) < 0.5)
                scroll.SetValue(WheelTargetProperty, double.NaN);
        };
        scroll.BeginAnimation(VerticalOffsetProperty, anim);
        return true;
    }

    /// <summary>
    /// Distance one notch travels, derived from the user's own Windows
    /// setting. People set WheelScrollLines deliberately — some to a single
    /// line, some to a whole page — and taking over the animation does not
    /// entitle us to overwrite that. A negative value is Windows' "one screen
    /// at a time".
    /// </summary>
    private static double NotchDistance(ScrollViewer scroll) =>
        NotchDistanceForLines(SystemParameters.WheelScrollLines, scroll.ViewportHeight);

    /// <summary>Pure so every branch is testable without changing an OS
    /// setting: a positive line count, Windows' "one screen at a time" (-1),
    /// and a nonsense zero.</summary>
    internal static double NotchDistanceForLines(int lines, double viewportHeight)
    {
        if (lines < 0)
            return viewportHeight > 0
                ? viewportHeight
                : WheelFeel.LineHeightDip * WheelFeel.FallbackLines;
        if (lines == 0)
            lines = WheelFeel.FallbackLines; // don't scroll nowhere
        return lines * WheelFeel.LineHeightDip;
    }

    /// <summary>
    /// Only animate deltas that look like a physical mouse wheel: a whole
    /// number of 120-unit notches.
    ///
    /// A precision touchpad sends many small, arbitrary deltas and is already
    /// smooth; animating each one stacks 120 ms tails on top of each other and
    /// turns it to mush with visible lag. Those pass straight through to
    /// native handling.
    ///
    /// The rule is deliberately conservative in that direction: passing too
    /// much through just means today's behaviour, while animating too much is
    /// a regression the owner feels immediately. NOTE: this could not be tested
    /// on a real touchpad — the build machine has two HID mice and no touchpad
    /// attached — so it is written to fail safe rather than to be clever.
    /// </summary>
    private static bool IsMouseWheelShapedDelta(int delta) =>
        delta != 0
        && Math.Abs(delta) >= WheelFeel.WheelDelta
        && delta % WheelFeel.WheelDelta == 0;

    /// <summary>Forget any in-flight wheel target. Called when something other
    /// than the wheel takes charge of the offset — a thumb drag, a keyboard
    /// scroll, a conversation switch — so a queued target cannot later fling
    /// the view somewhere nobody asked for.</summary>
    public static void CancelWheel(ScrollViewer scroll)
    {
        scroll.SetValue(WheelTargetProperty, double.NaN);
        scroll.BeginAnimation(VerticalOffsetProperty, null);
    }

    /// <summary>Test seam: the pending wheel target, or NaN when idle.</summary>
    internal static double PendingWheelTarget(ScrollViewer scroll) =>
        (double)scroll.GetValue(WheelTargetProperty);

    /// <summary>Scroll to the bottom — animated when motion is enabled, else snap.</summary>
    public static void ScrollToBottom(ScrollViewer scroll)
    {
        // A jump-to-latest supersedes anything the wheel had queued.
        scroll.SetValue(WheelTargetProperty, double.NaN);
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

    /// <summary>Scroll to a specific offset — animated when motion is
    /// enabled, else snap. Used by the send-time turn anchor: moving to the
    /// new turn is a deliberate move the user asked for by sending, and a
    /// hard cut loses the sense that the thread scrolled. Honours
    /// MotionSettings, which is the desktop's reduced-motion switch.</summary>
    public static void ScrollTo(ScrollViewer scroll, double offset)
    {
        // The send-time anchor supersedes anything the wheel had queued.
        scroll.SetValue(WheelTargetProperty, double.NaN);
        offset = Math.Max(0, Math.Min(offset, scroll.ScrollableHeight));
        if (!MotionSettings.Enabled)
        {
            scroll.BeginAnimation(VerticalOffsetProperty, null);
            scroll.ScrollToVerticalOffset(offset);
            return;
        }

        var anim = new DoubleAnimation(scroll.VerticalOffset, offset,
            new Duration(System.TimeSpan.FromMilliseconds(250)))
        {
            EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut }
        };
        scroll.BeginAnimation(VerticalOffsetProperty, anim);
    }
}
