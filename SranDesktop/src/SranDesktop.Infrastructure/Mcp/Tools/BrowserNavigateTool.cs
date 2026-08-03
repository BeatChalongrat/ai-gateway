using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_navigate (browser tools) — navigates and WAITS: the
/// extension resolves only after document.readyState === 'complete' plus a
/// short settle window, so the next read_page sees the real page.</summary>
public sealed class BrowserNavigateTool : IMcpTool
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserNavigateTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_navigate";
    public string Description =>
        "THE tool for opening a website you will then read, search or interact with: navigates the " +
        "user's paired Chrome to a URL and waits for the page to finish loading (returns the final " +
        "URL and title). Follow up with browser_read_page / browser_find / browser_click / " +
        "browser_type. Prefer this over open_url for every web task that needs you to see the page.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "url": { "type": "string", "description": "Absolute http:// or https:// URL" },
                "tab_id": { "type": "integer", "description": "Optional tab id (default: the active tab)" }
            },
            "required": ["url"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var url = ToolHelpers.GetString(arguments, "url");
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https"))
            return Task.FromResult(ToolResult.Fail("url must be an absolute http(s) URL."));

        return BrowserToolHelpers.RequestAsync(_bridge, "page.navigate",
            new { url, tab_id = BrowserToolHelpers.GetTabId(arguments) },
            r =>
            {
                // Surface the REAL tab id — without it the model invents one
                // (Chrome ids are arbitrary; "tab_id: 1" almost never exists).
                var tabId = BrowserToolHelpers.GetInt(r, "tab_id");
                var tabNote = tabId is { } id ? $"tab {id}" : "the current tab";
                return ToolResult.Ok(
                    $"Navigated {tabNote} to {BrowserToolHelpers.GetString(r, "url")} — " +
                    $"{BrowserToolHelpers.GetString(r, "title")}. " +
                    "Use browser_read_page to see its content" +
                    (tabId is { } tid
                        ? $" (pass tab_id {tid}, or omit tab_id to act on the current tab)."
                        : " (omit tab_id to act on the current tab)."));
            }, ct);
    }
}
