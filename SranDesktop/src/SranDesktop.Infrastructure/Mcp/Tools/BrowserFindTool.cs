using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_find (browser tools) — targeted lookup when the full
/// element list would be noise: matches element text/labels (or a CSS
/// selector) and returns just those refs.</summary>
public sealed class BrowserFindTool : IMcpTool, IBrowserTabAction
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserFindTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_find";
    public string Description =>
        "Find elements in a Chrome tab matching a text description or CSS selector. " +
        "Returns matching refs usable with browser_click / browser_type. " +
        BrowserToolHelpers.TabIdGuidance;
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "query": { "type": "string", "description": "Text to match against element labels, or a CSS selector" },
                "tab_id": { "type": "integer", "description": "Optional tab id (default: the active tab)" }
            },
            "required": ["query"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var query = ToolHelpers.GetString(arguments, "query");
        if (string.IsNullOrWhiteSpace(query))
            return ToolResult.Fail("query is required.");

        var siteGuard = await BrowserToolHelpers.EnforceAuthorizedSiteAsync(_bridge, BrowserToolHelpers.GetTabId(arguments), ct);
        if (siteGuard is not null) return siteGuard;

        return await BrowserToolHelpers.RequestAsync(_bridge, "page.find",
            new { query, tab_id = BrowserToolHelpers.GetTabId(arguments) },
            r => ToolResult.Ok(BrowserToolHelpers.GetString(r, "text")), ct);
    }
}
