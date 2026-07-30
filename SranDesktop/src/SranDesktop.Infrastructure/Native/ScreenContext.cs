namespace SranDesktop.Infrastructure.Native;

public enum CaptureType
{
    Window,
    Monitor,
    VirtualDesktop,
    DesktopOverview
}

public enum CoordinateSpace
{
    WindowImagePixels,
    MonitorImagePixels,
    VirtualDesktopImagePixels
}

public sealed record PhysicalRect(int X, int Y, int Width, int Height)
{
    public int Right => checked(X + Width);
    public int Bottom => checked(Y + Height);

    public bool Contains(int x, int y) =>
        x >= X && y >= Y && x < Right && y < Bottom;
}

/// <summary>
/// Immutable identity and geometry for one image delivered to the model.
/// Image coordinates are always pixels in that exact image. DPI is metadata;
/// it is never applied because SourceRectPhysical is already in physical pixels.
/// </summary>
public sealed record CaptureContext(
    string CaptureId,
    DateTimeOffset CapturedAtUtc,
    CaptureType CaptureType,
    int? ProcessId,
    long? WindowHandle,
    string? WindowTitle,
    string? MonitorId,
    CoordinateSpace CoordinateSpace,
    int ImageWidth,
    int ImageHeight,
    PhysicalRect SourceRectPhysical,
    double DpiScaleX,
    double DpiScaleY,
    long DisplayTopologyVersion,
    long WindowGeometryVersion);

public sealed record ImageRect(int X, int Y, int Width, int Height);

public sealed record TargetCoordinate(
    string CaptureId,
    int ImageX,
    int ImageY,
    ImageRect? BoundingBox = null,
    long? ExpectedWindowHandle = null,
    int? ExpectedProcessId = null);

/// <summary>Legacy mapping shape retained for source compatibility.</summary>
public sealed record ScreenMapping(
    double Scale,
    int OriginX,
    int OriginY,
    int CapturedWidth,
    int CapturedHeight,
    int ImageWidth,
    int ImageHeight)
{
    internal CaptureContext ToCaptureContext() => new(
        CaptureId: "legacy",
        CapturedAtUtc: DateTimeOffset.MinValue,
        CaptureType: CaptureType.VirtualDesktop,
        ProcessId: null,
        WindowHandle: null,
        WindowTitle: null,
        MonitorId: null,
        CoordinateSpace: CoordinateSpace.VirtualDesktopImagePixels,
        ImageWidth: ImageWidth,
        ImageHeight: ImageHeight,
        SourceRectPhysical: new PhysicalRect(
            OriginX, OriginY, CapturedWidth, CapturedHeight),
        DpiScaleX: 1,
        DpiScaleY: 1,
        DisplayTopologyVersion: 0,
        WindowGeometryVersion: 0);
}

/// <summary>The one authoritative image-to-physical coordinate mapper.</summary>
public static class CoordinateMapper
{
    public static (int X, int Y) ToPhysical(CaptureContext capture, int imageX, int imageY)
    {
        ArgumentNullException.ThrowIfNull(capture);
        if (capture.ImageWidth <= 0 || capture.ImageHeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(capture), "Image dimensions must be positive.");
        if (capture.SourceRectPhysical.Width <= 0 || capture.SourceRectPhysical.Height <= 0)
            throw new ArgumentOutOfRangeException(nameof(capture), "Source dimensions must be positive.");

        var scaleX = capture.SourceRectPhysical.Width / (double)capture.ImageWidth;
        var scaleY = capture.SourceRectPhysical.Height / (double)capture.ImageHeight;
        return (
            capture.SourceRectPhysical.X + (int)Math.Round(imageX * scaleX),
            capture.SourceRectPhysical.Y + (int)Math.Round(imageY * scaleY));
    }

    public static PhysicalRect? ToPhysical(
        CaptureContext capture, ImageRect imageRect, bool clampToCapture = true)
    {
        if (imageRect.Width <= 0 || imageRect.Height <= 0)
            return null;

        var (left, top) = ToPhysical(capture, imageRect.X, imageRect.Y);
        var (right, bottom) = ToPhysical(
            capture, imageRect.X + imageRect.Width, imageRect.Y + imageRect.Height);

        if (clampToCapture)
        {
            left = Math.Max(left, capture.SourceRectPhysical.X);
            top = Math.Max(top, capture.SourceRectPhysical.Y);
            right = Math.Min(right, capture.SourceRectPhysical.Right);
            bottom = Math.Min(bottom, capture.SourceRectPhysical.Bottom);
        }

        return right <= left || bottom <= top
            ? null
            : new PhysicalRect(left, top, right - left, bottom - top);
    }
}

/// <summary>
/// Capture store keyed by identity. Last is a compatibility affordance for old
/// tool calls; it still returns the full immutable capture context.
/// </summary>
public sealed class ScreenContext
{
    private const int MaxRetainedCaptures = 32;
    private readonly object _gate = new();
    private readonly Dictionary<string, CaptureContext> _captures = new(StringComparer.Ordinal);
    private CaptureContext? _last;

    public CaptureContext? Last
    {
        get
        {
            lock (_gate)
                return _last;
        }
    }

    public void Set(CaptureContext capture)
    {
        ArgumentNullException.ThrowIfNull(capture);
        lock (_gate)
        {
            _captures[capture.CaptureId] = capture;
            _last = capture;
            if (_captures.Count <= MaxRetainedCaptures)
                return;

            foreach (var id in _captures.Values
                         .OrderBy(c => c.CapturedAtUtc)
                         .Take(_captures.Count - MaxRetainedCaptures)
                         .Select(c => c.CaptureId)
                         .ToArray())
                _captures.Remove(id);
        }
    }

    public bool TryGet(string captureId, out CaptureContext? capture)
    {
        lock (_gate)
            return _captures.TryGetValue(captureId, out capture);
    }

    internal static (int X, int Y) ToScreenFromCapture(CaptureContext? capture, int x, int y) =>
        capture is null ? (x, y) : CoordinateMapper.ToPhysical(capture, x, y);

    internal static (int X, int Y) ToScreen(ScreenMapping? mapping, int x, int y) =>
        mapping is null ? (x, y) : CoordinateMapper.ToPhysical(mapping.ToCaptureContext(), x, y);

    internal static bool InVirtualScreen(
        int x, int y, (int X, int Y, int Width, int Height) rect) =>
        x >= rect.X && y >= rect.Y && x < rect.X + rect.Width && y < rect.Y + rect.Height;

    internal static PhysicalRect? MapZoomRegion(
        CaptureContext capture, int x, int y, int width, int height) =>
        CoordinateMapper.ToPhysical(capture, new ImageRect(x, y, width, height));

    internal static (int X, int Y, int Width, int Height)? MapZoomRegion(
        ScreenMapping mapping, int x, int y, int width, int height)
    {
        var rect = CoordinateMapper.ToPhysical(
            mapping.ToCaptureContext(), new ImageRect(x, y, width, height));
        return rect is null ? null : (rect.X, rect.Y, rect.Width, rect.Height);
    }
}
