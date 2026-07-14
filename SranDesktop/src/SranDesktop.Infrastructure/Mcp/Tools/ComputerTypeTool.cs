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
        "Type text into whatever currently has keyboard focus. " +
        "For keyboard shortcuts or special keys use computer_key instead.";
    public ToolCategory Category => ToolCategory.Screen;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "text": { "type": "string", "description": "The text to type. Newlines become Enter presses." }
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

        await Task.Run(() => Win32Input.TypeText(text), ct);
        var preview = text.Length > 80 ? text[..80] + "..." : text;
        return ToolResult.Ok($"Typed {text.Length} characters: \"{preview}\"");
    }

    /// <summary>Hard safety block: credentials never flow through synthetic typing.</summary>
    internal static bool LooksLikeCredential(string text, out string kind)
    {
        kind = "";
        // API-key shapes (vendor prefixes + long opaque tokens)
        if (Regex.IsMatch(text, @"\b(sk-|gw_|ghp_|github_pat_|xox[bap]-|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,})"))
        {
            kind = "API key or access token";
            return true;
        }
        // Credit-card numbers: 13-19 digits allowing spaces/dashes
        if (Regex.IsMatch(text, @"\b(?:\d[ -]?){13,19}\b") && Regex.IsMatch(text, @"\d{13,}|\d{4}[ -]\d{4}"))
        {
            kind = "credit card number";
            return true;
        }
        // Explicit password mentions alongside a value
        if (Regex.IsMatch(text, @"(?i)\b(password|passwd|pwd)\b\s*[:=]\s*\S+"))
        {
            kind = "password";
            return true;
        }
        return false;
    }
}
