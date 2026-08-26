using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerScrollTool : IMcpTool
{
    private readonly ScreenContext _screen;

    public ComputerScrollTool(ScreenContext screen) => _screen = screen;

    public string Name => "computer_scroll";
    public string Description =>
        "Scroll the mouse wheel at the given coordinates FROM THE LAST SCREENSHOT " +
        "(moves the cursor there first). Pass coordinates exactly as seen — never rescale.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowOnce;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "x": { "type": "integer", "description": "X coordinate in pixels in the last screenshot" },
                "y": { "type": "integer", "description": "Y coordinate in pixels in the last screenshot" },
                "direction": { "type": "string", "enum": ["up", "down", "left", "right"], "description": "Default down" },
                "amount": { "type": "integer", "description": "Wheel notches, 1-10 (default 3)" }
            },
            "required": ["x", "y"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!ComputerClickTool.TryGetScreenPoint(arguments, _screen, out var x, out var y, out var error))
            return ToolResult.Fail(error!);

        var direction = ToolHelpers.GetString(arguments, "direction") ?? "down";
        var amount = 3;
        if (arguments.TryGetProperty("amount", out var amountEl) && amountEl.TryGetInt32(out var a))
            amount = Math.Clamp(a, 1, 10);

        await Task.Run(() => Win32Input.Scroll(x, y, direction, amount), ct);
        await Task.Delay(100, ct);
        return ToolResult.Ok($"Scrolled {direction} by {amount} at screen point ({x}, {y}).");
    }
}
