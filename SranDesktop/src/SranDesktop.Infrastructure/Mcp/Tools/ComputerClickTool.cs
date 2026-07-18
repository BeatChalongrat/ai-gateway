using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerClickTool : IMcpTool
{
    private readonly ScreenContext _screen;
    private readonly IForegroundTargetTracker? _foreground;

    public ComputerClickTool(ScreenContext screen, IForegroundTargetTracker? foreground = null)
    {
        _screen = screen;
        _foreground = foreground;
    }

    public string Name => "computer_click";
    public string Description =>
        "Click the mouse at the given coordinates FROM THE LAST SCREENSHOT — pass exactly " +
        "what you see in the image, never rescale. Always take a screenshot first.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "x": { "type": "integer", "description": "X coordinate in pixels from the left edge of the last screenshot" },
                "y": { "type": "integer", "description": "Y coordinate in pixels from the top edge of the last screenshot" },
                "button": { "type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button (default left)" },
                "click_type": { "type": "string", "enum": ["single", "double", "triple"], "description": "Default single" }
            },
            "required": ["x", "y"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!TryGetScreenPoint(arguments, _screen, out var x, out var y, out var error))
            return ToolResult.Fail(error!);

        // Refuse clicking SRAN's own window: the agent fighting its host app
        // (accidentally pressing its own Stop/Send) is never what the user
        // asked for and can wedge the very turn that is executing. Restoring
        // the recorded target makes the refusal ACTIONABLE — the very next
        // screenshot shows the right app again instead of looping on SRAN.
        if (Win32Input.ProcessIdAtPoint(x, y) == (uint)Environment.ProcessId)
        {
            var restored = _foreground?.RestoreTarget() ?? false;
            return ToolResult.Fail(OwnWindowRefusal(restored));
        }

        var button = ToolHelpers.GetString(arguments, "button") ?? "left";
        var clicks = (ToolHelpers.GetString(arguments, "click_type") ?? "single") switch
        {
            "double" => 2,
            "triple" => 3,
            _ => 1
        };

        await Task.Run(() => Win32Input.Click(x, y, button, clicks), ct);
        await Task.Delay(100, ct); // let the UI respond
        return ToolResult.Ok($"Clicked {button} ({clicks}x) at screen point ({x}, {y}). " +
                             "Take a screenshot to verify the result.");
    }

    /// <summary>Own-window refusal text — actionable when the target app could
    /// be restored (the model's obvious next step is a fresh screenshot).</summary>
    internal static string OwnWindowRefusal(bool restored) => restored
        ? "That point is over SRAN's own window. The target app has been restored to the " +
          "foreground — take a fresh screenshot and try again."
        : "That point is over SRAN's own window — clicking the host app is not allowed. " +
          "Take a fresh screenshot of the target app and use coordinates from it.";

    /// <summary>Reads x/y (last-screenshot space), transforms them to screen
    /// space via the session mapping, and validates against the VIRTUAL
    /// desktop — negative coordinates are legal (monitor left of / above the
    /// primary). Shared with scroll/drag.</summary>
    internal static bool TryGetScreenPoint(
        JsonElement args, ScreenContext screen, out int x, out int y, out string? error,
        string xName = "x", string yName = "y")
    {
        x = y = 0;
        error = null;
        if (args.ValueKind != JsonValueKind.Object
            || !args.TryGetProperty(xName, out var xEl) || !xEl.TryGetInt32(out var imageX)
            || !args.TryGetProperty(yName, out var yEl) || !yEl.TryGetInt32(out var imageY))
        {
            error = $"{xName} and {yName} integer coordinates are required.";
            return false;
        }

        (x, y) = ScreenContext.ToScreen(screen.Last, imageX, imageY);

        var rect = Win32Input.VirtualScreenBounds();
        if (!ScreenContext.InVirtualScreen(x, y, rect))
        {
            error = $"({imageX}, {imageY}) maps to screen point ({x}, {y}), outside the desktop " +
                    $"(x {rect.X}..{rect.X + rect.Width - 1}, y {rect.Y}..{rect.Y + rect.Height - 1}). " +
                    "Take a fresh screenshot and use coordinates from it.";
            return false;
        }
        return true;
    }
}
