using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerDragTool : IMcpTool
{
    public string Name => "computer_drag";
    public string Description =>
        "Click and drag the mouse from one point to another (e.g. to select text or move an item).";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "start_x": { "type": "integer" },
                "start_y": { "type": "integer" },
                "end_x": { "type": "integer" },
                "end_y": { "type": "integer" }
            },
            "required": ["start_x", "start_y", "end_x", "end_y"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        int startX, startY, endX, endY;
        try
        {
            startX = arguments.GetProperty("start_x").GetInt32();
            startY = arguments.GetProperty("start_y").GetInt32();
            endX = arguments.GetProperty("end_x").GetInt32();
            endY = arguments.GetProperty("end_y").GetInt32();
        }
        catch (Exception)
        {
            return ToolResult.Fail("start_x, start_y, end_x, end_y integer coordinates are required.");
        }

        var (width, height) = Win32Input.PrimaryScreenSize();
        if (startX < 0 || startY < 0 || startX >= width || startY >= height
            || endX < 0 || endY < 0 || endX >= width || endY >= height)
            return ToolResult.Fail($"Coordinates are outside the screen bounds ({width}x{height}).");

        await Task.Run(() => Win32Input.Drag(startX, startY, endX, endY), ct);
        await Task.Delay(100, ct);
        return ToolResult.Ok($"Dragged from ({startX}, {startY}) to ({endX}, {endY}).");
    }
}
