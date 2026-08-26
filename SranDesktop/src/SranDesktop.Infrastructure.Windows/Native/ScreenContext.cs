namespace SranDesktop.Infrastructure.Native;

/// <summary>
/// How the LAST screenshot maps back to physical screen coordinates
/// (computer-use accuracy overhaul). <c>Scale</c> = captured/image ≥ 1, so
/// <c>screen = origin + round(image * Scale)</c>. Origin is in virtual-desktop
/// coordinates: (left,top) of the virtual desktop for full captures (can be
/// negative with a monitor left/above primary), the window's top-left for
/// window captures.
/// </summary>
public sealed record ScreenMapping(
    double Scale, int OriginX, int OriginY,
    int CapturedWidth, int CapturedHeight,
    int ImageWidth, int ImageHeight);

/// <summary>
/// Session state shared by the computer-use tools: the last screenshot's
/// coordinate mapping. Click/scroll/drag transform model-provided coordinates
/// (screenshot space) through it automatically, so the model NEVER rescales —
/// the old "multiply by X" instruction was routinely ignored and clicks landed
/// at a fraction of the target. App-global rather than per-conversation on
/// purpose: there is one physical screen and one foreground user, and a
/// screenshot from conversation A describes the same pixels conversation B
/// would capture.
/// </summary>
public sealed class ScreenContext
{
    private volatile ScreenMapping? _last;

    /// <summary>Mapping of the most recent screenshot; null before the first
    /// capture of the session.</summary>
    public ScreenMapping? Last => _last;

    public void Set(ScreenMapping mapping) => _last = mapping;

    /// <summary>Screenshot space → screen space. With no mapping (no prior
    /// screenshot this session) coordinates pass through unchanged — the
    /// legacy behavior, so a model driving from memory/other context still
    /// lands where it aimed.</summary>
    internal static (int X, int Y) ToScreen(ScreenMapping? mapping, int x, int y) =>
        mapping is null
            ? (x, y)
            : (mapping.OriginX + (int)Math.Round(x * mapping.Scale),
               mapping.OriginY + (int)Math.Round(y * mapping.Scale));

    /// <summary>Bounds check against the virtual desktop, which may start at
    /// negative coordinates (monitor left of / above the primary).</summary>
    internal static bool InVirtualScreen(int x, int y, (int X, int Y, int Width, int Height) rect) =>
        x >= rect.X && y >= rect.Y && x < rect.X + rect.Width && y < rect.Y + rect.Height;

    /// <summary>
    /// Maps a zoom region (last-screenshot space) to a screen-space rect,
    /// clamped to the captured area. Null when the region misses the capture
    /// entirely. Used by computer_zoom.
    /// </summary>
    internal static (int X, int Y, int Width, int Height)? MapZoomRegion(
        ScreenMapping mapping, int x, int y, int width, int height)
    {
        if (width <= 0 || height <= 0)
            return null;

        var left = mapping.OriginX + (int)Math.Round(x * mapping.Scale);
        var top = mapping.OriginY + (int)Math.Round(y * mapping.Scale);
        var right = mapping.OriginX + (int)Math.Round((x + width) * mapping.Scale);
        var bottom = mapping.OriginY + (int)Math.Round((y + height) * mapping.Scale);

        // Clamp to the captured area — a region that pokes past the screenshot
        // edge is trimmed rather than rejected.
        left = Math.Max(left, mapping.OriginX);
        top = Math.Max(top, mapping.OriginY);
        right = Math.Min(right, mapping.OriginX + mapping.CapturedWidth);
        bottom = Math.Min(bottom, mapping.OriginY + mapping.CapturedHeight);

        return right - left < 1 || bottom - top < 1
            ? null
            : (left, top, right - left, bottom - top);
    }
}
