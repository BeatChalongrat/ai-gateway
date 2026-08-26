using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>browser_screenshot (browser tools) — captureVisibleTab through the
/// extension (JPEG, to stay under the 1 MB native-messaging frame cap).
/// Complements browser_read_page for visual verification.</summary>
public sealed class BrowserScreenshotTool : IMcpTool, IBrowserTabAction
{
    private readonly IBrowserBridgeService _bridge;

    public BrowserScreenshotTool(IBrowserBridgeService bridge) => _bridge = bridge;

    public string Name => "browser_screenshot";
    public string Description =>
        "Take a screenshot of a Chrome tab's visible area. Prefer browser_read_page for " +
        "interaction; use this to visually verify a page's state. " +
        BrowserToolHelpers.TabIdGuidance;
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "tab_id": { "type": "integer", "description": "Optional tab id (default: the active tab; a given tab is activated first)" }
            }
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var siteGuard = await BrowserToolHelpers.EnforceAuthorizedSiteAsync(_bridge, BrowserToolHelpers.GetTabId(arguments), ct);
        if (siteGuard is not null) return siteGuard;

        return await BrowserToolHelpers.RequestAsync(_bridge, "page.screenshot",
            new { tab_id = BrowserToolHelpers.GetTabId(arguments) },
            r =>
            {
                var image = BrowserToolHelpers.GetString(r, "image");
                if (!image.StartsWith("data:image/", StringComparison.Ordinal))
                    return ToolResult.Fail("The extension returned no image.");
                return ToolResult.OkWithImage(
                    $"Screenshot of tab: {BrowserToolHelpers.GetString(r, "title")}. " +
                    "The image follows in the next message.", image);
            }, ct);
    }
}
