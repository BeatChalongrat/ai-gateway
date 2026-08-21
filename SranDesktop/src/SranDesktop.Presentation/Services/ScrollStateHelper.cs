using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Animation;

namespace SranDesktop.Presentation.Services;

/// <summary>
/// Cubic-bezier timing evaluation, the same shape CSS and Chromium use: the
/// curve runs from (0,0) to (1,1) through control points (x1,y1) and (x2,y2),
/// x is the time fraction and y the progress fraction.
///
/// Only needed to answer one question — how fast is a running scroll animation
/// moving right now — so that a retarget can carry that speed into the next
/// curve instead of restarting from a standstill.
/// </summary>
internal static class CubicBezier
{
    private static double SampleX(double t, double x1, double x2) =>
        3 * (1 - t) * (1 - t) * t * x1 + 3 * (1 - t) * t * t * x2 + t * t * t;

    private static double SampleDerivativeX(double t, double x1, double x2) =>
        3 * (1 - t) * (1 - t) * x1 + 6 * (1 - t) * t * (x2 - x1) + 3 * t * t * (1 - x2);

    private static double SampleDerivativeY(double t, double y1, double y2) =>
        3 * (1 - t) * (1 - t) * y1 + 6 * (1 - t) * t * (y2 - y1) + 3 * t * t * (1 - y2);

    /// <summary>Curve parameter t at which the curve reaches time fraction
    /// <paramref name="x"/>. Newton first, bisection as the guaranteed
    /// fallback when the derivative is too flat to iterate on.</summary>
    internal static double SolveForT(double x, double x1, double x2)
    {
        x = Math.Clamp(x, 0.0, 1.0);
        var t = x;
        for (var i = 0; i < 8; i++)
        {
            var error = SampleX(t, x1, x2) - x;
            if (Math.Abs(error) < 1e-6)
                return t;
            var slope = SampleDerivativeX(t, x1, x2);
            if (Math.Abs(slope) < 1e-6)
                break;
            t -= error / slope;
        }

        double low = 0, high = 1;
        t = x;
        for (var i = 0; i < 32; i++)
        {
            var sample = SampleX(t, x1, x2);
            if (Math.Abs(sample - x) < 1e-6)
                return t;
            if (sample < x) low = t; else high = t;
            t = (low + high) / 2;
        }
        return t;
    }

    /// <summary>dy/dx at time fraction <paramref name="x"/> — i.e. progress per
    /// unit of time, which scaled by (distance / duration) is velocity.</summary>
    internal static double SlopeAtX(double x1, double y1, double x2, double y2, double x)
    {
        var t = SolveForT(x, x1, x2);
        var dx = SampleDerivativeX(t, x1, x2);
        if (Math.Abs(dx) < 1e-6)
            return 0;
        return SampleDerivativeY(t, y1, y2) / dx;
    }
}

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
    /// Why any of this exists, in two parts. WPF applies a wheel notch in a
    /// single frame — the view is at offset X, then at X+80, with nothing in
    /// between — and it moves only ~16 DIP per configured line where a browser
    /// moves 40. So the same hand gesture both jumped and fell short. These
    /// constants are Chromium's own, verified against its source; the comments
    /// say which file each came from.
    /// </summary>
    public static class WheelFeel
    {
        /// <summary>
        /// DIP per line of wheel travel. This is Chromium's
        /// <c>kPixelsPerLineStep</c> (cc/input/scroll_utils.h), NOT WPF's ~16:
        /// WPF moves 48 DIP at the Windows default of 3 lines where a browser
        /// moves 120, so the same hand gesture reached 2.5x less far. That
        /// difference needs no animation to fix and is very likely the largest
        /// single part of "ไม่เลื่อนเท่าพวก chatgpt claude".
        /// </summary>
        public const double PixelsPerLineStep = 40.0;

        /// <summary>Multiplier on the derived distance. 1.0 = exactly
        /// Chromium's reach. Tune here, not in the arithmetic.</summary>
        public const double DistanceMultiplier = 1.0;

        /// <summary>Fallback when the OS reports a nonsense line count.</summary>
        public const int FallbackLines = 3;

        /// <summary>One physical notch. Windows reports wheel movement in
        /// units of WHEEL_DELTA = 120.</summary>
        public const int WheelDelta = 120;

        // ── duration: inverse-delta ─────────────────────────────────────
        // Chromium's DurationBehavior::kInverseDelta for wheel scrolls
        // (cc/animation/scroll_offset_animation_curve.cc). The FARTHER it has
        // to travel, the SHORTER the animation — which is what makes rapid
        // wheeling accelerate instead of pile up lag. Delta is the ACCUMULATED
        // pending distance, not the single notch.
        //
        //   units    = clamp(14 - |delta| / 60, 6, 12)
        //   duration = units / 60 seconds
        //
        // 120 px -> 200 ms · 240 px -> 167 ms · 480 px and beyond -> 100 ms.
        public const double InverseDeltaRampStartPx = 120.0;
        public const double InverseDeltaRampEndPx = 480.0;
        public const double InverseDeltaMinDuration = 6.0;
        public const double InverseDeltaMaxDuration = 12.0;
        public const double DurationDivisor = 60.0;

        // ── easing: CSS ease-in-out, symmetric ──────────────────────────
        // cubic-bezier(0.42, 0, 0.58, 1) — Chromium's kEaseInOutControlPoints.
        // Symmetric, so the curve STARTS from zero velocity; that is what
        // gives browser scrolling its sense of mass. An EaseOut curve leaves
        // at maximum velocity, which from a standing start reads as a snap.
        //
        // Deliberately NOT applied to ScrollTo/ScrollToBottom: a jump the app
        // initiates should arrive gently, not depart gently, so those keep
        // their EaseOut.
        public const double EaseX1 = 0.42;
        public const double EaseY1 = 0.0;
        public const double EaseX2 = 0.58;
        public const double EaseY2 = 1.0;

        /// <summary>Chromium clamps the retarget slope to this.</summary>
        public const double MaxRetargetSlope = 1000.0;
    }

    /// <summary>Duration for a pending travel distance, in seconds. Pure and
    /// internal so the ramp can be checked at its endpoints.</summary>
    internal static double WheelDurationSeconds(double pendingDistancePx)
    {
        var distance = Math.Abs(pendingDistancePx);
        var slope = (WheelFeel.InverseDeltaMinDuration - WheelFeel.InverseDeltaMaxDuration)
                    / (WheelFeel.InverseDeltaRampEndPx - WheelFeel.InverseDeltaRampStartPx);
        var offset = WheelFeel.InverseDeltaMaxDuration - WheelFeel.InverseDeltaRampStartPx * slope;
        var units = Math.Clamp(offset + distance * slope,
            WheelFeel.InverseDeltaMinDuration, WheelFeel.InverseDeltaMaxDuration);
        return units / WheelFeel.DurationDivisor;
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
    private static readonly DependencyProperty WheelFlightProperty =
        DependencyProperty.RegisterAttached(
            "WheelFlight", typeof(WheelFlight), typeof(AnimatedScroller),
            new PropertyMetadata(null));

    /// <summary>Everything needed to retarget mid-flight WITHOUT losing the
    /// speed already built up: where the current curve started, where it is
    /// heading, when it started, how long it runs, and the initial slope it
    /// was built with.</summary>
    private sealed record WheelFlight(
        double StartOffset, double Target, long StartTick, double DurationSec, double Slope);

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
        if (!IsMouseWheelShapedDelta(delta))
            return false;

        if (scroll.ScrollableHeight <= 0)
            return false;

        var distance = NotchDistance(scroll) * WheelFeel.DistanceMultiplier;
        var travel = -(delta / (double)WheelFeel.WheelDelta) * distance;

        // Accumulate toward the PENDING target when one is in flight, so a
        // burst of notches adds up instead of each one restarting from
        // wherever the animation happens to have got to.
        var flight = (WheelFlight?)scroll.GetValue(WheelFlightProperty);
        var basis = flight?.Target ?? scroll.VerticalOffset;

        // Clamp at ACCUMULATION time, not only when animating: otherwise
        // wheeling hard at the end of a thread builds an invisible overshoot
        // the user then has to unwind before anything moves.
        var target = Math.Max(0, Math.Min(basis + travel, scroll.ScrollableHeight));

        var from = scroll.VerticalOffset;
        if (Math.Abs(target - from) < 0.5 && flight is null)
            return false; // already at the end this notch aims at

        // DISTANCE IS NOT MOTION. Reduced motion removes the animation, not the
        // reach: the notch still travels the full Chromium distance, it just
        // arrives in one frame. Gating the distance on the animation would
        // leave reduced-motion users with WPF's short 16-px-per-line scroll.
        if (!MotionSettings.Enabled)
        {
            CancelWheel(scroll);
            scroll.ScrollToVerticalOffset(target);
            return true;
        }

        var pending = target - from;
        var durationSec = WheelDurationSeconds(pending);
        var slope = RetargetSlope(scroll, flight, pending, durationSec);

        scroll.SetValue(WheelFlightProperty,
            new WheelFlight(from, target, Environment.TickCount64, durationSec, slope));

        scroll.BeginAnimation(VerticalOffsetProperty,
            BuildWheelAnimation(scroll, from, target, durationSec, slope));
        return true;
    }

    /// <summary>
    /// The initial slope for a retargeted curve, so speed already built up
    /// carries into the new one. Chromium:
    /// <c>new_slope = velocity * (new_duration / |new_delta|)</c>, clamped.
    /// Without it every notch restarts from zero velocity and rapid wheeling
    /// stutters once per notch.
    /// </summary>
    private static double RetargetSlope(
        ScrollViewer scroll, WheelFlight? flight, double pending, double durationSec)
    {
        if (flight is null || durationSec <= 0 || Math.Abs(pending) < 0.5)
            return 0; // from rest: the symmetric curve starts at zero velocity

        var elapsed = (Environment.TickCount64 - flight.StartTick) / 1000.0;
        if (elapsed <= 0 || elapsed >= flight.DurationSec || flight.DurationSec <= 0)
            return 0;

        // Velocity in px/s = total travel x (dy/dx at this point) / duration.
        var dydx = CubicBezier.SlopeAtX(
            WheelFeel.EaseX1, WheelFeel.EaseX1 * flight.Slope,
            WheelFeel.EaseX2, WheelFeel.EaseY2,
            elapsed / flight.DurationSec);
        var velocity = (flight.Target - flight.StartOffset) * dydx / flight.DurationSec;

        var slope = velocity * (durationSec / Math.Abs(pending));
        return Math.Clamp(slope, -WheelFeel.MaxRetargetSlope, WheelFeel.MaxRetargetSlope);
    }

    /// <summary>
    /// The exact cubic-bezier(0.42, 0, 0.58, 1) curve, expressed as WPF's
    /// KeySpline rather than approximated with a built-in easing class. A
    /// retarget scales the first control point's y by the slope, exactly as
    /// Chromium's EaseInOutWithInitialSlope does.
    /// </summary>
    private static AnimationTimeline BuildWheelAnimation(
        ScrollViewer scroll, double from, double target, double durationSec, double slope)
    {
        // KeySpline requires both control-point coordinates within [0,1], so a
        // very fast retarget cannot be expressed exactly; y1 saturates at 1,
        // i.e. slopes above 1/0.42 = 2.38 flatten to that. Measured on
        // realistic notch bursts this does not bind (see SmoothWheelTests).
        var y1 = Math.Clamp(WheelFeel.EaseX1 * slope, 0.0, 1.0);
        var spline = new KeySpline(WheelFeel.EaseX1, y1, WheelFeel.EaseX2, WheelFeel.EaseY2);
        var duration = System.TimeSpan.FromSeconds(durationSec);

        var anim = new DoubleAnimationUsingKeyFrames { Duration = new Duration(duration) };
        // A new animation otherwise starts from the property's BASE value, not
        // from where the previous animation had actually got to.
        anim.KeyFrames.Add(new LinearDoubleKeyFrame(from, KeyTime.FromTimeSpan(System.TimeSpan.Zero)));
        anim.KeyFrames.Add(new SplineDoubleKeyFrame(target, KeyTime.FromTimeSpan(duration), spline));
        anim.Completed += (_, _) =>
        {
            // Only clear if this flight is still the one in charge; a newer
            // notch or an anchor scroll may have moved the target on.
            if (scroll.GetValue(WheelFlightProperty) is WheelFlight current
                && Math.Abs(current.Target - target) < 0.5)
                scroll.SetValue(WheelFlightProperty, null);
        };
        return anim;
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
                : WheelFeel.PixelsPerLineStep * WheelFeel.FallbackLines;
        if (lines == 0)
            lines = WheelFeel.FallbackLines; // don't scroll nowhere
        return lines * WheelFeel.PixelsPerLineStep;
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
        scroll.SetValue(WheelFlightProperty, null);
        DetachAnimationInPlace(scroll);
    }

    /// <summary>
    /// Stop animating without moving.
    ///
    /// Detaching an animation reverts the attached property to its LOCAL
    /// value, and nothing ever assigns one — so the effective value drops from
    /// wherever the animation had it to the default 0, the property-changed
    /// callback runs, and the view is yanked to the top of the thread.
    /// Measured, not theorised: cancelling at offset 980 landed at 0.
    ///
    /// Pinning the local value to the real offset FIRST makes the removal a
    /// no-op — the effective value never changes, so the callback never fires.
    /// </summary>
    private static void DetachAnimationInPlace(ScrollViewer scroll)
    {
        scroll.SetValue(VerticalOffsetProperty, scroll.VerticalOffset);
        scroll.BeginAnimation(VerticalOffsetProperty, null);
    }

    /// <summary>Test seam: the pending wheel target, or NaN when idle.</summary>
    internal static double PendingWheelTarget(ScrollViewer scroll) =>
        scroll.GetValue(WheelFlightProperty) is WheelFlight f ? f.Target : double.NaN;

    /// <summary>Test seam: the initial slope the in-flight curve was built
    /// with. 0 means it started from rest.</summary>
    internal static double PendingWheelSlope(ScrollViewer scroll) =>
        scroll.GetValue(WheelFlightProperty) is WheelFlight f ? f.Slope : double.NaN;

    /// <summary>Test seam: the duration the in-flight curve was built with.</summary>
    internal static double PendingWheelDurationSec(ScrollViewer scroll) =>
        scroll.GetValue(WheelFlightProperty) is WheelFlight f ? f.DurationSec : double.NaN;

    /// <summary>Scroll to the bottom — animated when motion is enabled, else snap.</summary>
    public static void ScrollToBottom(ScrollViewer scroll)
    {
        // A jump-to-latest supersedes anything the wheel had queued.
        scroll.SetValue(WheelFlightProperty, null);
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
        scroll.SetValue(WheelFlightProperty, null);
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
