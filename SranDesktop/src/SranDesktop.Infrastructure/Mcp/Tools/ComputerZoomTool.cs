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
/// computer_zoom — re-captures a region of the LAST screenshot at full native
/// resolution (computer-use accuracy overhaul): the downscaled overview keeps
/// token cost bounded, this recovers the detail. The region is re-grabbed live
/// from the screen (not cropped from the old bitmap), so it also doubles as a
/// cheap "did my click change anything here" check.
/// </summary>
public sealed class ComputerZoomTool : IMcpTool
{
    private readonly ScreenContext _screen;

    public ComputerZoomTool(ScreenContext screen) => _screen = screen;

    public string Name => "computer_zoom";
    public string Description =>
        "Zoom into a region of the last screenshot at full resolution to read small text " +
        "or find precise click targets.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "x": { "type": "integer", "description": "Region left edge, in last-screenshot pixels" },
                "y": { "type": "integer", "description": "Region top edge, in last-screenshot pixels" },
                "width": { "type": "integer", "description": "Region width, in last-screenshot pixels" },
                "height": { "type": "integer", "description": "Region height, in last-screenshot pixels" }
            },
            "required": ["x", "y", "width", "height"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var mapping = _screen.Last;
        if (mapping is null)
            return Task.FromResult(ToolResult.Fail(
                "No screenshot has been taken yet — call computer_screenshot first."));

        if (!TryGetInt(arguments, "x", out var x) || !TryGetInt(arguments, "y", out var y)
            || !TryGetInt(arguments, "width", out var width) || !TryGetInt(arguments, "height", out var height))
            return Task.FromResult(ToolResult.Fail("x, y, width and height integers are required."));

        var region = ScreenContext.MapZoomRegion(mapping, x, y, width, height);
        if (region is null)
            return Task.FromResult(ToolResult.Fail(
                $"Region ({x},{y} {width}x{height}) lies outside the last screenshot " +
                $"({mapping.ImageWidth}x{mapping.ImageHeight})."));

        try
        {
            var (screenX, screenY, w, h) =
                (region.X, region.Y, region.Width, region.Height);
            using var bitmap = new Bitmap(w, h);
            using (var graphics = Graphics.FromImage(bitmap))
                graphics.CopyFromScreen(screenX, screenY, 0, 0, bitmap.Size);

            using var memory = new MemoryStream();
            bitmap.Save(memory, ImageFormat.Png);
            var dataUri = "data:image/png;base64,"
                + Convert.ToBase64String(memory.GetBuffer(), 0, (int)memory.Length);

            return Task.FromResult(ToolResult.OkWithImage(
                $"Zoomed region {w}x{h} at full resolution (from screen point ({screenX}, {screenY})). " +
                "NOTE: coordinates inside this zoom are NOT clickable directly — use coordinates " +
                "from the last full screenshot for clicks.",
                dataUri,
                [$"zoom_region_observed={w}x{h}", $"zoom_origin={screenX},{screenY}"]));
        }
        catch (Exception ex)
        {
            return Task.FromResult(ToolResult.Fail($"Zoom capture failed: {ex.Message}"));
        }
    }

    private static bool TryGetInt(JsonElement args, string name, out int value)
    {
        value = 0;
        return args.ValueKind == JsonValueKind.Object
            && args.TryGetProperty(name, out var el)
            && el.TryGetInt32(out value);
    }
}
