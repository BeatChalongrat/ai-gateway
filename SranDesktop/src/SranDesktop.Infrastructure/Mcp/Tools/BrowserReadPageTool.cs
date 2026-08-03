using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_read_page (browser tools) — THE preferred way to see a
/// page: the content script returns an indexed interactive-element list
/// ([12] &lt;button&gt; "Send reply" (enabled)) whose refs feed browser_click /
/// browser_type. DOM-level, so it beats screenshots for accuracy.</summary>
public sealed class BrowserReadPageTool : IMcpTool
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserReadPageTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_read_page";
    public string Description =>
        "Read a Chrome tab: indexed list of interactive elements (use the [n] refs with " +
        "browser_click / browser_type) plus the page text with filter=full-text. " +
        "Refs go stale when the page changes — re-read after navigation. " +
        BrowserToolHelpers.TabIdGuidance;
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "tab_id": { "type": "integer", "description": "Optional tab id (default: the active tab)" },
                "filter": { "type": "string", "enum": ["interactive", "full-text"], "description": "interactive (default) = elements only; full-text adds the page text" }
            }
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct) =>
        BrowserToolHelpers.RequestAsync(_bridge, "page.read",
            new
            {
                tab_id = BrowserToolHelpers.GetTabId(arguments),
                filter = ToolHelpers.GetString(arguments, "filter") ?? "interactive"
            },
            r => ToolResult.Ok(BrowserToolHelpers.GetString(r, "text")), ct);
}
