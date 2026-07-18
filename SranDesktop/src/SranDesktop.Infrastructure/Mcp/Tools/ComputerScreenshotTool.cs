using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// The agent's eyes: captures the FULL virtual desktop (all monitors) or one
/// window, scales it down to bound token cost, and records the image→screen
/// mapping in <see cref="ScreenContext"/> so click/scroll/drag translate
/// coordinates automatically — the model must never rescale (the old
/// "multiply by X" instruction was routinely ignored → misclicks).
/// </summary>
public sealed class ComputerScreenshotTool : IMcpTool
{
    /// <summary>Downscale cap on the LONGER side. Width-only 1920 made a
    /// 5760-wide triple-monitor desktop a 3x shrink — text unreadable. 2560 on
    /// the longer side keeps multi-monitor text legible while bounding token
    /// cost; a single 1920/2560 monitor still ships unscaled, and precise
    /// reading is computer_zoom's job.</summary>
    internal const int MaxLongSide = 2560;

    private readonly ScreenContext _screen;
    private readonly IForegroundTargetTracker? _foreground;

    public ComputerScreenshotTool(ScreenContext screen, IForegroundTargetTracker? foreground = null)
    {
        _screen = screen;
        _foreground = foreground;
    }

    public string Name => "computer_screenshot";
    public string Description =>
        "Take a screenshot of the screen. Returns the image for visual analysis. " +
        "TIP: for Windows applications, read_screen_elements + uia_click is usually more " +
        "accurate than looking at pixels — use screenshots when UIA gives poor results " +
        "(games, custom-drawn apps, Chrome without accessibility) or to visually verify " +
        "an action's outcome.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "window_title": {
                    "type": "string",
                    "description": "Optional: capture only the window whose title contains this text (else the full desktop across all monitors)"
                }
            }
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        try
        {
            var windowTitle = ToolHelpers.GetString(arguments, "window_title");
            int originX = 0, originY = 0;
            Bitmap? bitmap;
            if (windowTitle is { Length: > 0 })
            {
                bitmap = CaptureWindow(windowTitle, out originX, out originY);
                if (bitmap is null)
                    return Task.FromResult(ToolResult.Fail(
                        $"Could not capture window containing '{windowTitle}'. Is it open and visible?"));
            }
            else
            {
                // Self-interference fix: if SRAN itself holds the foreground
                // (a consent dialog just closed), a full-screen shot would show
                // SRAN instead of the app the agent is working on — put the
                // recorded target back first.
                if (_foreground?.RestoreIfOwnForeground() == true)
                    Thread.Sleep(200); // let the target repaint before the grab
                bitmap = CaptureVirtualDesktop(out originX, out originY);
            }

            using var source = bitmap;
            using var scaled = ScaleDown(source, MaxLongSide);

            // Every capture rewrites the session mapping — clicks always refer
            // to the LAST screenshot the model saw.
            _screen.Set(new ScreenMapping(
                Scale: source.Width / (double)scaled.Width,
                OriginX: originX, OriginY: originY,
                CapturedWidth: source.Width, CapturedHeight: source.Height,
                ImageWidth: scaled.Width, ImageHeight: scaled.Height));

            // Encode straight to a data URI in memory — no temp file (Phase 6
            // Task 4): the old write-then-read roundtrip leaked a PNG per shot.
            using var memory = new MemoryStream();
            scaled.Save(memory, ImageFormat.Png);
            var dataUri = "data:image/png;base64," + Convert.ToBase64String(memory.GetBuffer(), 0, (int)memory.Length);

            return Task.FromResult(ToolResult.OkWithImage(
                $"Screenshot captured: {scaled.Width}x{scaled.Height}. Coordinates you see in " +
                "this image can be used directly with computer_click — do NOT rescale them.",
                dataUri));
        }
        catch (Exception ex)
        {
            return Task.FromResult(ToolResult.Fail($"Screenshot failed: {ex.Message}"));
        }
    }

    /// <summary>Full virtual desktop — all monitors, including ones left of /
    /// above the primary (negative origin).</summary>
    private static Bitmap CaptureVirtualDesktop(out int originX, out int originY)
    {
        var (x, y, width, height) = Win32Input.VirtualScreenBounds();
        originX = x;
        originY = y;
        var bitmap = new Bitmap(width, height);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.CopyFromScreen(x, y, 0, 0, bitmap.Size);
        return bitmap;
    }

    /// <summary>PrintWindow capture. The window's screen top-left goes into the
    /// mapping origin so window-relative pixels translate to correct absolute
    /// click coordinates (the old code returned the bitmap with no offset —
    /// clicks on window screenshots landed shifted).</summary>
    private static Bitmap? CaptureWindow(string titlePart, out int originX, out int originY)
    {
        originX = originY = 0;
        var hWnd = Win32Input.FindWindowByTitle(titlePart);
        if (hWnd == IntPtr.Zero)
            return null;

        Win32Input.GetWindowRect(hWnd, out var rect);
        var width = rect.Right - rect.Left;
        var height = rect.Bottom - rect.Top;
        if (width <= 0 || height <= 0)
            return null;

        originX = rect.Left;
        originY = rect.Top;
        var bitmap = new Bitmap(width, height);
        using var graphics = Graphics.FromImage(bitmap);
        var hdc = graphics.GetHdc();
        try
        {
            Win32Input.PrintWindow(hWnd, hdc, 2); // PW_RENDERFULLCONTENT
        }
        finally
        {
            graphics.ReleaseHdc(hdc);
        }
        return bitmap;
    }

    private static Bitmap ScaleDown(Bitmap source, int maxLongSide)
    {
        var longSide = Math.Max(source.Width, source.Height);
        if (longSide <= maxLongSide)
            return (Bitmap)source.Clone();

        var scale = maxLongSide / (double)longSide;
        var scaled = new Bitmap(
            Math.Max(1, (int)(source.Width * scale)),
            Math.Max(1, (int)(source.Height * scale)));
        using var graphics = Graphics.FromImage(scaled);
        graphics.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
        graphics.DrawImage(source, 0, 0, scaled.Width, scaled.Height);
        return scaled;
    }
}
