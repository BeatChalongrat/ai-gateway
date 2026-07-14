using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerClickTool : IMcpTool
{
    public string Name => "computer_click";
    public string Description =>
        "Click the mouse at the given screen coordinates (pixels from the top-left). " +
        "Always take a screenshot first — never guess coordinates.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "x": { "type": "integer", "description": "X coordinate in pixels from the left edge" },
                "y": { "type": "integer", "description": "Y coordinate in pixels from the top edge" },
                "button": { "type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button (default left)" },
                "click_type": { "type": "string", "enum": ["single", "double", "triple"], "description": "Default single" }
            },
            "required": ["x", "y"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!TryGetPoint(arguments, out var x, out var y, out var error))
            return ToolResult.Fail(error!);

        var button = ToolHelpers.GetString(arguments, "button") ?? "left";
        var clicks = (ToolHelpers.GetString(arguments, "click_type") ?? "single") switch
        {
            "double" => 2,
            "triple" => 3,
            _ => 1
        };

        await Task.Run(() => Win32Input.Click(x, y, button, clicks), ct);
        await Task.Delay(100, ct); // let the UI respond
        return ToolResult.Ok($"Clicked {button} ({clicks}x) at ({x}, {y}). " +
                             "Take a screenshot to verify the result.");
    }

    internal static bool TryGetPoint(JsonElement args, out int x, out int y, out string? error)
    {
        x = y = 0;
        error = null;
        if (args.ValueKind != JsonValueKind.Object
            || !args.TryGetProperty("x", out var xEl) || !xEl.TryGetInt32(out x)
            || !args.TryGetProperty("y", out var yEl) || !yEl.TryGetInt32(out y))
        {
            error = "x and y integer coordinates are required.";
            return false;
        }

        var (width, height) = Win32Input.PrimaryScreenSize();
        if (x < 0 || y < 0 || x >= width || y >= height)
        {
            error = $"Coordinates ({x}, {y}) are outside the screen bounds ({width}x{height}).";
            return false;
        }
        return true;
    }
}
