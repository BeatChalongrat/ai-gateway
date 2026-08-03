using System.Diagnostics;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Hand a link to the user. Routing fix (post-8a28026): while the Chrome
/// extension is CONNECTED, the URL opens as a new tab in the user's paired
/// Chrome via the bridge — never the OS default browser (which may be Edge and
/// is invisible to the browser_* tools). The default-browser Process.Start is
/// only the fallback for when the bridge is down, and its result says so.
/// </summary>
public sealed class OpenUrlTool : IMcpTool
{
    private readonly IBrowserBridgeService? _bridge;
    private readonly IBrowserLauncher? _launcher;

    public OpenUrlTool(IBrowserBridgeService? bridge = null, IBrowserLauncher? launcher = null)
    {
        _bridge = bridge;
        _launcher = launcher;
    }

    public string Name => "open_url";
    public string Description =>
        "Open a link for the user to LOOK AT themselves. When the SRAN Chrome extension is connected " +
        "this opens a new Chrome tab; otherwise it falls back to the user's default browser, where you " +
        "CANNOT see or interact with the page. For any web task where YOU need to read, search, click " +
        "or fill in the page afterwards, use browser_navigate (and the other browser_* tools) instead.";
    public ToolCategory Category => ToolCategory.Browser;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "url": { "type": "string", "description": "http:// or https:// URL to open" }
            },
            "required": ["url"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var url = ToolHelpers.GetString(arguments, "url");
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https"))
        {
            return ToolResult.Fail("URL must be absolute and use http:// or https://.");
        }

        // Preferred path: the user's configured Chrome, through the extension.
        // tabs.new (not page.navigate): opening a link must never clobber
        // whatever the user's active tab is showing.
        if (_bridge is { IsConnected: true })
        {
            try
            {
                var result = await _bridge.RequestAsync(
                    "tabs.new", new { url = uri.AbsoluteUri }, ct);
                var title = BrowserToolHelpers.GetString(result, "title");
                return ToolResult.Ok(
                    $"Opened {uri.AbsoluteUri} in a new Chrome tab"
                    + (title.Length > 0 ? $" — {title}" : "")
                    + ". The page is available to you now: use browser_read_page / browser_find / "
                    + "browser_click to continue working with it.");
            }
            catch (BrowserNotConnectedException)
            {
                // Bridge dropped between the check and the call — fall through
                // to the default-browser fallback below.
            }
            catch (TimeoutException ex)
            {
                return ToolResult.Fail(ex.Message);
            }
            catch (BrowserBridgeException ex)
            {
                return ToolResult.Fail(ex.Message);
            }
        }

        try
        {
            var opened = _launcher?.Open(uri.AbsoluteUri)
                         ?? StartDefaultBrowser(uri.AbsoluteUri);
            if (!opened)
                return ToolResult.Fail("Could not open a browser for this link.");
            return ToolResult.Ok(
                $"Opened {uri.AbsoluteUri} in the user's DEFAULT browser (the SRAN Chrome extension "
                + "is not connected). You cannot see or interact with that page. If the task needs "
                + "you to read or act on the page, tell the user to connect the Chrome extension "
                + "(Settings → Browser) and then use browser_navigate.");
        }
        catch (Exception ex)
        {
            return ToolResult.Fail($"Could not open browser: {ex.Message}");
        }
    }

    private static bool StartDefaultBrowser(string url)
    {
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        return true;
    }
}
