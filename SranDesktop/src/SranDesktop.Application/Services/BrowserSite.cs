using System.Text.Json;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Application.Services;

/// <summary>
/// FIX6-addendum — the AUTHORITATIVE per-site gate is fail-CLOSED, symmetric with
/// PathGuard's grants gate (E4 §1 #4): the failure mode is refusal, not permission.
/// The layer exists to catch a HOST bug, and a host bug is exactly the case where
/// the authorized origin is absent — so a missing origin must REFUSE, never pass.
/// </summary>
public static class BrowserSiteGate
{
    /// <summary>True when a browser action must be REFUSED for lack of an authorized
    /// origin: the tool is in the tab-acting family (data: <see cref="IBrowserTabAction"/>)
    /// AND the host supplied no authorized site. Tools outside the family are never
    /// refused here (they gate elsewhere, or not at all).</summary>
    public static bool RequiresRefusal(IMcpTool tool, string? authorizedSite) =>
        tool is IBrowserTabAction && string.IsNullOrEmpty(authorizedSite);

    /// <summary>The legible refusal message for a missing authorized origin.</summary>
    public const string MissingSiteMessage =
        "authorized-site-missing: this browser action targets a tab and must carry the origin the " +
        "host authorized (_meta.authorizedSite). None was supplied, so it is refused rather than run.";
}

/// <summary>
/// FIX6 — the per-site permission SCOPE for a browser action. The grant unit is
/// the ORIGIN (scheme://host[:port], default ports omitted), NOT the registrable
/// domain: subdomains and http-vs-https are separate trust boundaries, so each is
/// granted independently. Kept in lock-step with the host's TypeScript
/// <c>siteScope()</c> (apps/desktop/src/main/permissions.ts) via a shared test
/// vector; the sidecar re-derives the LIVE origin of the tab an action targets and
/// refuses it if it does not match the origin the host authorized (closing the
/// redirect / TOCTOU window). The sidecar is the authoritative gate — the host's
/// resolution is for the prompt; this one guards the act.
/// </summary>
public static class BrowserSite
{
    /// <summary>The origin scope for a url, or null if unparseable. http(s) →
    /// scheme://host[:port] (default ports omitted, host lowercased — matching
    /// JS <c>URL.origin</c>); any other scheme → "scheme:" so chrome:/about:/file:
    /// are distinct, promptable scopes.</summary>
    public static string? SiteScope(string? rawUrl)
    {
        if (string.IsNullOrWhiteSpace(rawUrl)) return null;
        if (!Uri.TryCreate(rawUrl, UriKind.Absolute, out var u)) return null;
        if (u.Scheme is "http" or "https")
        {
            var port = u.IsDefaultPort ? "" : ":" + u.Port;
            return $"{u.Scheme}://{u.Host.ToLowerInvariant()}{port}";
        }
        return u.Scheme.ToLowerInvariant() + ":";
    }

    /// <summary>The url of the tab matching <paramref name="tabId"/>, or the active
    /// tab when null, from a bridge <c>tabs.list</c> result. Null if not found.
    /// Shared by the sidecar's per-action site resolver (for the host's prompt) and
    /// the tab-acting tools' live-origin enforcement (for the act).</summary>
    public static string? ResolveTabUrl(JsonElement tabsResult, int? tabId)
    {
        if (tabsResult.ValueKind != JsonValueKind.Object
            || !tabsResult.TryGetProperty("tabs", out var tabs)
            || tabs.ValueKind != JsonValueKind.Array)
            return null;
        foreach (var t in tabs.EnumerateArray())
        {
            if (tabId is { } want)
            {
                if (t.TryGetProperty("id", out var idEl) && idEl.TryGetInt32(out var id) && id == want)
                    return Url(t);
            }
            else if (t.TryGetProperty("active", out var a) && a.ValueKind == JsonValueKind.True)
            {
                return Url(t);
            }
        }
        return null;

        static string? Url(JsonElement t) =>
            t.TryGetProperty("url", out var el) && el.ValueKind == JsonValueKind.String ? el.GetString() : null;
    }
}
