using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_type (browser tools) — sets an input's value through the
/// content script (React-safe native setter + input events). Shares the
/// desktop credential guard; the content script ADDITIONALLY refuses fields
/// whose name/id/type look like credential fields regardless of the text.</summary>
public sealed class BrowserTypeTool : IMcpTool
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserTypeTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_type";
    public string Description =>
        "Type text into an input element by its [n] ref from browser_read_page. " +
        "clear=true replaces the existing value (default); submit=true presses Enter after typing. " +
        BrowserToolHelpers.TabIdGuidance;
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "ref": { "type": "integer", "description": "Element ref from browser_read_page / browser_find" },
                "text": { "type": "string", "description": "The text to type" },
                "clear": { "type": "boolean", "description": "Replace the current value first (default true)" },
                "submit": { "type": "boolean", "description": "Press Enter after typing (default false)" },
                "tab_id": { "type": "integer", "description": "Optional tab id (default: the active tab)" }
            },
            "required": ["ref", "text"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!arguments.TryGetProperty("ref", out var refEl) || !refEl.TryGetInt32(out var elementRef))
            return Task.FromResult(ToolResult.Fail("ref integer is required."));
        var text = ToolHelpers.GetString(arguments, "text");
        if (text is null)
            return Task.FromResult(ToolResult.Fail("text is required."));

        if (Application.Services.CredentialTextGuard.LooksLikeCredential(text, out var kind))
            return Task.FromResult(ToolResult.Fail(
                $"Refused: the text looks like a {kind}. Typing credentials, keys, or card " +
                "numbers is prohibited — ask the user to enter it themselves."));

        return BrowserToolHelpers.RequestAsync(_bridge, "page.type",
            new
            {
                @ref = elementRef,
                text,
                clear = ToolHelpers.GetBool(arguments, "clear", fallback: true),
                submit = ToolHelpers.GetBool(arguments, "submit"),
                tab_id = BrowserToolHelpers.GetTabId(arguments)
            },
            _ =>
            {
                var preview = text.Length > 60 ? text[..60] + "…" : text;
                return ToolResult.Ok($"Typed into [{elementRef}]: \"{preview}\".");
            }, ct);
    }
}
