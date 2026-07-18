using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerDragTool : IMcpTool
{
    private readonly ScreenContext _screen;

    public ComputerDragTool(ScreenContext screen) => _screen = screen;

    public string Name => "computer_drag";
    public string Description =>
        "Click and drag the mouse from one point to another (e.g. to select text or move an item). " +
        "Coordinates are FROM THE LAST SCREENSHOT — pass them exactly as seen, never rescale.";
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
        if (!ComputerClickTool.TryGetScreenPoint(arguments, _screen,
                out var startX, out var startY, out var error, "start_x", "start_y"))
            return ToolResult.Fail(error!);
        if (!ComputerClickTool.TryGetScreenPoint(arguments, _screen,
                out var endX, out var endY, out error, "end_x", "end_y"))
            return ToolResult.Fail(error!);

        await Task.Run(() => Win32Input.Drag(startX, startY, endX, endY), ct);
        await Task.Delay(100, ct);
        return ToolResult.Ok($"Dragged from screen point ({startX}, {startY}) to ({endX}, {endY}).");
    }
}
