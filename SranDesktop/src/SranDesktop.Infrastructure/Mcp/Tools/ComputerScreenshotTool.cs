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
/// The agent's eyes: captures the screen (or one window), scales it down to
/// bound token cost, and returns the image for vision analysis.
/// </summary>
public sealed class ComputerScreenshotTool : IMcpTool
{
    private const int MaxWidth = 1920;

    public string Name => "computer_screenshot";
    public string Description =>
        "Take a screenshot of the screen. Returns the image for visual analysis. " +
        "Always use this before clicking to see what's on screen and find exact coordinates.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "window_title": {
                    "type": "string",
                    "description": "Optional: capture only the window whose title contains this text (else the full primary screen)"
                }
            }
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        try
        {
            var windowTitle = ToolHelpers.GetString(arguments, "window_title");
            using var bitmap = windowTitle is { Length: > 0 }
                ? CaptureWindow(windowTitle, out var error)
                : CaptureScreen();
            if (bitmap is null)
                return Task.FromResult(ToolResult.Fail(
                    $"Could not capture window containing '{windowTitle}'. Is it open and visible?"));

            using var scaled = ScaleDown(bitmap, MaxWidth);

            // Encode straight to a data URI in memory — no temp file (Phase 6
            // Task 4): the old write-then-read roundtrip leaked a PNG per shot.
            using var memory = new MemoryStream();
            scaled.Save(memory, ImageFormat.Png);
            var dataUri = "data:image/png;base64," + Convert.ToBase64String(memory.GetBuffer(), 0, (int)memory.Length);

            var note = scaled.Width != bitmap.Width
                ? $" (scaled from {bitmap.Width}x{bitmap.Height} — multiply coordinates you see by {bitmap.Width / (double)scaled.Width:0.###} to click)"
                : "";
            return Task.FromResult(ToolResult.OkWithImage(
                $"Screenshot captured: {scaled.Width}x{scaled.Height}{note}. " +
                "The image follows in the next message.",
                dataUri));
        }
        catch (Exception ex)
        {
            return Task.FromResult(ToolResult.Fail($"Screenshot failed: {ex.Message}"));
        }
    }

    private static Bitmap CaptureScreen()
    {
        var (width, height) = Win32Input.PrimaryScreenSize();
        var bitmap = new Bitmap(width, height);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.CopyFromScreen(0, 0, 0, 0, bitmap.Size);
        return bitmap;
    }

    private static Bitmap? CaptureWindow(string titlePart, out string? error)
    {
        error = null;
        var hWnd = Win32Input.FindWindowByTitle(titlePart);
        if (hWnd == IntPtr.Zero)
        {
            error = "window not found";
            return null;
        }

        Win32Input.GetWindowRect(hWnd, out var rect);
        var width = rect.Right - rect.Left;
        var height = rect.Bottom - rect.Top;
        if (width <= 0 || height <= 0)
        {
            error = "window has no visible area";
            return null;
        }

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

    private static Bitmap ScaleDown(Bitmap source, int maxWidth)
    {
        if (source.Width <= maxWidth)
            return (Bitmap)source.Clone();

        var scale = maxWidth / (double)source.Width;
        var scaled = new Bitmap(maxWidth, Math.Max(1, (int)(source.Height * scale)));
        using var graphics = Graphics.FromImage(scaled);
        graphics.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
        graphics.DrawImage(source, 0, 0, scaled.Width, scaled.Height);
        return scaled;
    }
}
