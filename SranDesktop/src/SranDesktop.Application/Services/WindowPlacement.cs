namespace SranDesktop.Application.Services;

/// <summary>A saved window rectangle plus whether it was maximized.</summary>
public readonly record struct WindowBounds(
    double Left, double Top, double Width, double Height, bool IsMaximized);

/// <summary>One monitor's working area, as the restore logic sees it.</summary>
public readonly record struct ScreenArea(
    double Left, double Top, double Width, double Height)
{
    public double Right => Left + Width;
    public double Bottom => Top + Height;
}

/// <summary>
/// Decides where the main window opens (Phase 11 Task 2).
///
/// Pure and screen-agnostic — the caller supplies the monitors — so the
/// interesting part (a saved position that no longer fits the current
/// monitor set) is unit-testable without a desktop session.
///
/// The rule that matters: a restore must never put the window somewhere the
/// user cannot reach it. Unplugging a second monitor, dropping the
/// resolution, or a rectangle saved while the window straddled two screens
/// must all end with a window that is visible and grabbable.
/// </summary>
public static class WindowPlacement
{
    /// <summary>How much of the title bar has to remain on a monitor for the
    /// window to count as reachable. Anything less and the user cannot drag
    /// it back, which is indistinguishable from "the app didn't open".</summary>
    public const double MinVisible = 80;

    /// <summary>
    /// Returns the bounds to open with, or null when there is nothing usable
    /// saved (fresh install, corrupt values) — the caller then keeps the
    /// XAML defaults and its CenterScreen startup location.
    /// </summary>
    public static WindowBounds? Restore(
        WindowBounds? saved,
        IReadOnlyList<ScreenArea> screens,
        double minWidth,
        double minHeight)
    {
        if (saved is not { } b || screens.Count == 0)
            return null;

        // Reject garbage rather than trying to rescue it: NaN/∞ come from a
        // hand-edited or half-written settings file, and a zero-size window
        // is not something the user ever produced.
        if (!IsFinite(b.Left) || !IsFinite(b.Top) || !IsFinite(b.Width) || !IsFinite(b.Height))
            return null;
        if (b.Width <= 0 || b.Height <= 0)
            return null;

        // A maximized window only needs a monitor to be maximized ON; its
        // restore rectangle is still clamped so un-maximizing lands somewhere
        // sensible.
        var width = Math.Max(b.Width, minWidth);
        var height = Math.Max(b.Height, minHeight);

        var host = HostScreen(b, screens) ?? screens[0];

        // Never larger than the monitor it will live on.
        width = Math.Min(width, host.Width);
        height = Math.Min(height, host.Height);

        var left = b.Left;
        var top = b.Top;

        // Pull the window fully onto the host monitor when it hangs off an
        // edge far enough to be unusable.
        if (left + width < host.Left + MinVisible) left = host.Left;
        if (left > host.Right - MinVisible) left = host.Right - width;
        // The title bar is at the TOP, so a window pushed above the work area
        // is unreachable even if most of its body is visible.
        if (top < host.Top) top = host.Top;
        if (top > host.Bottom - MinVisible) top = host.Bottom - height;

        // Final guarantee: REACHABLE, not fully contained. A window the user
        // deliberately parked so it overhangs the work area a little is not a
        // problem to fix — moving it would be the surprising behaviour. Only
        // the two things that make a window ungrabbable are enforced: enough
        // width on screen to click, and a title bar below the top edge.
        left = Math.Min(Math.Max(left, host.Left - (width - MinVisible)), host.Right - MinVisible);
        top = Math.Min(Math.Max(top, host.Top), Math.Max(host.Top, host.Bottom - MinVisible));

        return new WindowBounds(left, top, width, height, b.IsMaximized);
    }

    /// <summary>The monitor the saved rectangle overlaps most, or null when it
    /// overlaps none (the monitor it used to live on is gone).</summary>
    private static ScreenArea? HostScreen(WindowBounds b, IReadOnlyList<ScreenArea> screens)
    {
        ScreenArea? best = null;
        double bestArea = 0;
        foreach (var s in screens)
        {
            var overlap = Math.Max(0, Math.Min(b.Left + b.Width, s.Right) - Math.Max(b.Left, s.Left))
                        * Math.Max(0, Math.Min(b.Top + b.Height, s.Bottom) - Math.Max(b.Top, s.Top));
            if (overlap > bestArea)
            {
                bestArea = overlap;
                best = s;
            }
        }
        return best;
    }

    private static bool IsFinite(double v) => !double.IsNaN(v) && !double.IsInfinity(v);
}
