using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_click (browser tools) — clicks a ref from the last
/// browser_read_page. The content script re-verifies the node is alive and
/// visible (stale refs error cleanly) and the extension waits out any
/// navigation the click starts, returning the final URL + title.</summary>
public sealed class BrowserClickTool : IMcpTool, IBrowserTabAction
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserClickTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_click";
    public string Description =>
        "Click an element in a Chrome tab by its [n] ref from browser_read_page. " +
        "If the click navigates, waits for the new page and returns its URL and title. " +
        BrowserToolHelpers.TabIdGuidance;
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "ref": { "type": "integer", "description": "Element ref from browser_read_page / browser_find" },
                "tab_id": { "type": "integer", "description": "Optional tab id (default: the active tab)" }
            },
            "required": ["ref"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!arguments.TryGetProperty("ref", out var refEl) || !refEl.TryGetInt32(out var elementRef))
            return ToolResult.Fail("ref integer is required.");

        var siteGuard = await BrowserToolHelpers.EnforceAuthorizedSiteAsync(_bridge, BrowserToolHelpers.GetTabId(arguments), ct);
        if (siteGuard is not null) return siteGuard;

        return await BrowserToolHelpers.RequestAsync(_bridge, "page.click",
            new { @ref = elementRef, tab_id = BrowserToolHelpers.GetTabId(arguments) },
            r =>
            {
                var navigated = r.TryGetProperty("navigated", out var nav) && nav.ValueKind == JsonValueKind.True;
                return ToolResult.Ok(navigated
                    ? $"Clicked [{elementRef}] — navigated to: {BrowserToolHelpers.GetString(r, "title")} — " +
                      $"{BrowserToolHelpers.GetString(r, "url")}. Refs are now stale; call browser_read_page again."
                    : $"Clicked [{elementRef}]. Re-read the page to see the result.");
            }, ct);
    }
}
