using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerKeyTool : IMcpTool
{
    public string Name => "computer_key";
    public string Description =>
        "Press a keyboard key or shortcut, e.g. 'Enter', 'Ctrl+C', 'Alt+Tab', 'Ctrl+Shift+S'. " +
        "Pass window_title to focus a specific window first (verified). " +
        "Use for shortcuts and special keys; use computer_type for literal text.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "keys": { "type": "string", "description": "Key or combo, e.g. 'Enter', 'Ctrl+C', 'Alt+F4'" },
                "window_title": { "type": "string", "description": "Optional: focus the window whose title contains this text before pressing" }
            },
            "required": ["keys"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var keys = ToolHelpers.GetString(arguments, "keys");
        if (string.IsNullOrWhiteSpace(keys))
            return ToolResult.Fail("keys is required.");

        try
        {
            // Parse first so an unknown key fails cleanly without touching input.
            Win32Input.ParseKeyCombo(keys);
        }
        catch (ArgumentException ex)
        {
            return ToolResult.Fail(ex.Message);
        }

        if (ToolHelpers.GetString(arguments, "window_title") is { Length: > 0 } windowTitle
            && await WindowFocus.FocusWindowForInputAsync(windowTitle, ct) is { } focusError)
            return ToolResult.Fail(focusError);

        await Task.Run(() => Win32Input.PressKey(keys), ct);
        await Task.Delay(80, ct);
        return ToolResult.Ok($"Pressed {keys}.");
    }
}
