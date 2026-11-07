using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>Shared plumbing for the browser_* tools: one place that turns
/// bridge failures into the messages the model can act on (install guidance,
/// timeout, extension-reported errors passed through verbatim).</summary>
internal static class BrowserToolHelpers
{
    internal const string NotConnectedMessage =
        "Chrome extension not connected — ask the user to install/enable it (Settings → Browser).";

    internal static async Task<ToolResult> RequestAsync(
        IBrowserBridgeService bridge, string method, object? @params,
        Func<JsonElement, ToolResult> onSuccess, CancellationToken ct)
    {
        try
        {
            var result = await bridge.RequestAsync(method, @params, ct);
            return onSuccess(result);
        }
        catch (BrowserNotConnectedException)
        {
            return ToolResult.Fail(NotConnectedMessage);
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

    /// <summary>Shared steering line for every tab-scoped browser tool: the
    /// model must never invent a tab id (Chrome ids are arbitrary numbers).</summary>
    internal const string TabIdGuidance =
        "Leave tab_id empty to act on the CURRENT tab (usually the one you just navigated); " +
        "only pass a tab_id that came from browser_navigate or browser_list_tabs — never invent one.";

    /// <summary>The integer the bridge result carries under <paramref name="name"/>, or null.</summary>
    internal static int? GetInt(JsonElement result, string name) =>
        result.ValueKind == JsonValueKind.Object
        && result.TryGetProperty(name, out var el)
        && el.TryGetInt32(out var value)
            ? value
            : null;

    /// <summary>FIX6: before a tab-acting browser tool acts, verify the target
    /// tab's LIVE origin matches the origin the host authorized for this call
    /// (<see cref="ShellSession.AmbientAuthorizedSite"/>). Returns a Fail result on
    /// a mismatch (or if the tab/site can't be resolved), else null (proceed). This
    /// is the AUTHORITATIVE gate: it closes the window between the host's per-site
    /// prompt and the act, so a page that redirected across the origin boundary —
    /// or a tab swapped underneath — is refused rather than acted on. navigate and
    /// list_tabs do NOT call this (navigate goes TO the site; list_tabs enumerates).</summary>
    internal static async Task<ToolResult?> EnforceAuthorizedSiteAsync(
        IBrowserBridgeService bridge, int? tabId, CancellationToken ct)
    {
        var authorized = ShellSession.AmbientAuthorizedSite;
        // FIX6-addendum: fail-CLOSED. A tab-acting tool reaches here only WITH an
        // authorized origin (StdioMcpServer refuses a missing one before dispatch,
        // via BrowserSiteGate). If it is still empty, a host bug slipped through —
        // refuse rather than act, symmetric with PathGuard. Never return a pass.
        if (string.IsNullOrEmpty(authorized))
            return ToolResult.Fail(BrowserSiteGate.MissingSiteMessage);

        string? liveUrl;
        try
        {
            var tabs = await bridge.RequestAsync("tabs.list", null, ct);
            liveUrl = BrowserSite.ResolveTabUrl(tabs, tabId);
        }
        catch (BrowserNotConnectedException) { return ToolResult.Fail(NotConnectedMessage); }
        catch (Exception ex) { return ToolResult.Fail($"Could not verify the target tab before acting: {ex.Message}"); }

        var liveSite = BrowserSite.SiteScope(liveUrl);
        if (liveSite is null)
            return ToolResult.Fail("Could not determine the target tab's site; refusing to act.");
        if (!string.Equals(liveSite, authorized, StringComparison.OrdinalIgnoreCase))
            return ToolResult.Fail(
                $"Refused: the tab is now on {liveSite}, not the authorized site {authorized} " +
                "(it navigated after you were granted access). Re-read the page and ask again for this site.");
        return null; // origin matches — proceed
    }

    internal static int? GetTabId(JsonElement args) =>
        args.ValueKind == JsonValueKind.Object
        && args.TryGetProperty("tab_id", out var el)
        && el.TryGetInt32(out var id)
            ? id
            : null;

    internal static string GetString(JsonElement result, string name) =>
        result.ValueKind == JsonValueKind.Object
        && result.TryGetProperty(name, out var el)
        && el.ValueKind == JsonValueKind.String
            ? el.GetString() ?? ""
            : "";
}
