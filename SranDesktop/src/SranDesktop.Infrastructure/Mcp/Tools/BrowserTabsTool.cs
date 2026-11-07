using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_tabs — tab MANAGEMENT (browser tools): activate, open,
/// close. Listing lives in browser_list_tabs (AllowAlways); these act on the
/// user's browser → Ask.</summary>
public sealed class BrowserTabsTool : IMcpTool, IBrowserTabAction
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserTabsTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_tabs";
    public string Description =>
        "Manage Chrome tabs: activate (focus) a tab, open a new tab, or close one. " +
        "Use browser_list_tabs first to get tab ids.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "action": { "type": "string", "enum": ["activate", "new", "close"], "description": "What to do" },
                "tab_id": { "type": "integer", "description": "Target tab id (activate/close)" },
                "url": { "type": "string", "description": "URL for action=new (http/https)" }
            },
            "required": ["action"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var action = ToolHelpers.GetString(arguments, "action");
        var tabId = BrowserToolHelpers.GetTabId(arguments);
        var url = ToolHelpers.GetString(arguments, "url");

        switch (action)
        {
            case "activate" when tabId is null:
            case "close" when tabId is null:
                return ToolResult.Fail($"tab_id is required for action '{action}'.");
            case "new" when !Uri.TryCreate(url, UriKind.Absolute, out var uri)
                            || uri.Scheme is not ("http" or "https"):
                return ToolResult.Fail("url must be an absolute http(s) URL for action 'new'.");
            case "activate":
            case "close":
            {
                // FIX6: activate/close act ON a specific tab — verify that tab's
                // LIVE origin is the one the host authorized. ("new" opens a fresh
                // tab at a destination the host already gated, like navigate.)
                var siteGuard = await BrowserToolHelpers.EnforceAuthorizedSiteAsync(_bridge, tabId, ct);
                if (siteGuard is not null) return siteGuard;
                return action == "activate"
                    ? await BrowserToolHelpers.RequestAsync(_bridge, "tabs.activate", new { tab_id = tabId },
                        _ => ToolResult.Ok($"Activated tab {tabId}."), ct)
                    : await BrowserToolHelpers.RequestAsync(_bridge, "tabs.close", new { tab_id = tabId },
                        _ => ToolResult.Ok($"Closed tab {tabId}."), ct);
            }
            case "new":
                return await BrowserToolHelpers.RequestAsync(_bridge, "tabs.new", new { url },
                    r => ToolResult.Ok($"Opened tab {r.GetProperty("tab_id").GetInt32()}: " +
                                       $"{BrowserToolHelpers.GetString(r, "title")} — {BrowserToolHelpers.GetString(r, "url")}"), ct);
            default:
                return ToolResult.Fail("action must be one of: activate, new, close.");
        }
    }
}
