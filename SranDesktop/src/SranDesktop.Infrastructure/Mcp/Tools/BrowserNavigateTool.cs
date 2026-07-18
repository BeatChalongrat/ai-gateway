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
        "Navigate a Chrome tab to a URL and wait for the page to finish loading. " +
        "Returns the final URL and title.";
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
            r => ToolResult.Ok($"Loaded: {BrowserToolHelpers.GetString(r, "title")} — " +
                               $"{BrowserToolHelpers.GetString(r, "url")}. " +
                               "Use browser_read_page to see its content."), ct);
    }
}
