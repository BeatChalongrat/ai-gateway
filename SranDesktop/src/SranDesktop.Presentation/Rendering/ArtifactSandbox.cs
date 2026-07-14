using System.Text.RegularExpressions;

namespace SranDesktop.Presentation.Rendering;

/// <summary>What the WebView2 should do with a navigation request.</summary>
public enum ArtifactNavigation { Allow, Deny, OpenExternal }

/// <summary>
/// Pure sandbox policy for artifact WebView2 rendering (Phase 6 Task 3): the
/// virtual host that serves the bundled JS libs, the Content-Security-Policy
/// injected into every generated document, and the navigation allow/deny
/// decision. No WPF/WebView2 types here so it's unit-testable.
/// </summary>
public static class ArtifactSandbox
{
    /// <summary>Virtual host mapped to the bundled WebLibs folder (least
    /// privilege: only this host may load app assets).</summary>
    public const string VirtualHost = "appassets.sran";

    public const string LibBase = "https://appassets.sran";

    /// <summary>
    /// Locked-down CSP: no default fetch, scripts/styles only inline or from
    /// the bundled host, images from data: / https:, fonts from the host, and
    /// NO network connections. connect-src 'none' means artifacts cannot call
    /// external APIs — the intended sandbox posture (matches Claude artifacts).
    /// </summary>
    public const string ContentSecurityPolicy =
        "default-src 'none'; " +
        "script-src 'unsafe-inline' https://appassets.sran; " +
        "style-src 'unsafe-inline' https://appassets.sran; " +
        "img-src data: https:; " +
        "font-src https://appassets.sran; " +
        "connect-src 'none'";

    public static string CspMetaTag() =>
        $"<meta http-equiv=\"Content-Security-Policy\" content=\"{ContentSecurityPolicy}\">";

    private static readonly Regex HeadOpenRegex =
        new("<head[^>]*>", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex HtmlOpenRegex =
        new("<html[^>]*>", RegexOptions.IgnoreCase | RegexOptions.Compiled);

    /// <summary>
    /// Injects the CSP meta into an HTML document. If it has a &lt;head&gt;, the
    /// meta goes right after it; a document with &lt;html&gt; but no head gets a
    /// synthesized head; a bare fragment gets the meta prepended (browsers read
    /// a leading meta before body content).
    ///
    /// Idempotency is keyed on OUR EXACT meta only: a document that ships its
    /// own (possibly permissive) CSP still gets ours injected, because when
    /// multiple policies are present the browser enforces their INTERSECTION —
    /// an attacker's looser policy can never relax ours. (The old substring
    /// check let an artifact bypass the sandbox by carrying any CSP-shaped
    /// text.)
    /// </summary>
    public static string InjectCsp(string html)
    {
        if (html.Contains(CspMetaTag(), StringComparison.Ordinal))
            return html; // already carries OUR exact meta — idempotent re-render

        var meta = CspMetaTag();

        var headMatch = HeadOpenRegex.Match(html);
        if (headMatch.Success)
            return html.Insert(headMatch.Index + headMatch.Length, meta);

        var htmlMatch = HtmlOpenRegex.Match(html);
        if (htmlMatch.Success)
            return html.Insert(htmlMatch.Index + htmlMatch.Length, $"<head>{meta}</head>");

        return meta + html;
    }

    /// <summary>
    /// Navigation policy: the initial NavigateToString content (about:blank /
    /// data:) and the bundled-lib host are allowed; a cancelled http(s) target
    /// opens in the system browser; anything else is denied outright. This is a
    /// PURE URL policy — the caller additionally gates OpenExternal on a user
    /// gesture (see <see cref="ShouldOpenExternal"/>), which is WebView2 event
    /// state, not something the URL can tell us.
    /// </summary>
    public static ArtifactNavigation Decide(string? uri)
    {
        if (string.IsNullOrEmpty(uri))
            return ArtifactNavigation.Allow; // in-page/initial content

        if (uri.StartsWith("about:", StringComparison.OrdinalIgnoreCase)
            || uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase))
            return ArtifactNavigation.Allow;

        if (Uri.TryCreate(uri, UriKind.Absolute, out var parsed))
        {
            if (parsed.Scheme is "https" or "http"
                && parsed.Host.Equals(VirtualHost, StringComparison.OrdinalIgnoreCase))
                return ArtifactNavigation.Allow;

            if (parsed.Scheme is "https" or "http")
                return ArtifactNavigation.OpenExternal;
        }

        return ArtifactNavigation.Deny;
    }

    /// <summary>
    /// Escalate to the system browser ONLY for a user-initiated OpenExternal
    /// navigation. Artifact JS calling window.open / setting location.href with
    /// no user gesture must not pop the real browser (nuisance/phishing +
    /// query-string exfiltration) — those are denied silently.
    /// </summary>
    public static bool ShouldOpenExternal(ArtifactNavigation decision, bool isUserInitiated) =>
        decision == ArtifactNavigation.OpenExternal && isUserInitiated;
}
