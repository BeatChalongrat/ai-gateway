using System.Text.Json;
using System.Text.RegularExpressions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Native;

namespace SranDesktop.Infrastructure.Mcp.Tools;

public sealed class ComputerTypeTool : IMcpTool
{
    public string Name => "computer_type";
    public string Description =>
        "Type text into whatever currently has keyboard focus. Pass window_title to focus a " +
        "specific window first (verified — fails rather than typing into the wrong window). " +
        "For keyboard shortcuts or special keys use computer_key instead.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "text": { "type": "string", "description": "The text to type. Newlines become Enter presses." },
                "window_title": { "type": "string", "description": "Optional: focus the window whose title contains this text before typing" }
            },
            "required": ["text"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var text = ToolHelpers.GetString(arguments, "text");
        if (string.IsNullOrEmpty(text))
            return ToolResult.Fail("text is required.");

        if (LooksLikeCredential(text, out var kind))
            return ToolResult.Fail(
                $"Refused: the text looks like a {kind}. Typing credentials, keys, or card " +
                "numbers is prohibited — ask the user to enter it themselves.");

        if (ToolHelpers.GetString(arguments, "window_title") is { Length: > 0 } windowTitle
            && await ToolHelpers.FocusWindowForInputAsync(windowTitle, ct) is { } focusError)
            return ToolResult.Fail(focusError);

        await Task.Run(() => Win32Input.TypeText(text), ct);
        var preview = text.Length > 80 ? text[..80] + "..." : text;
        return ToolResult.Ok($"Typed {text.Length} characters: \"{preview}\"");
    }

    /// <summary>Hard safety block: credentials never flow through synthetic
    /// typing. Delegates to the shared <see cref="Application.Services.CredentialTextGuard"/>
    /// (one list for computer_type / uia_set_value / browser_type).</summary>
    internal static bool LooksLikeCredential(string text, out string kind) =>
        Application.Services.CredentialTextGuard.LooksLikeCredential(text, out kind);
}
