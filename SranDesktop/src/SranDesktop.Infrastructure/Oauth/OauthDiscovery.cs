using System.Text.RegularExpressions;

namespace SranDesktop.Infrastructure.Oauth;

/// <summary>
/// FIX7 — the pure parsing + selection halves of lazy authentication (RFC 9728 →
/// RFC 8414/OIDC). Kept free of I/O so the exact behaviour the source specifies is
/// unit-tested: the <c>WWW-Authenticate</c> parse, the "first authorization_servers
/// entry, no fallback" rule, and the scope selection (the 401's scope wins; else the
/// PRM's <c>scopes_supported</c>; and <c>offline_access</c> is appended only when the
/// AS advertises it, to obtain a refresh token). The network fetches live in
/// <see cref="ClientNativeOauthFlow"/>.
/// </summary>
public static class OauthDiscovery
{
    // WWW-Authenticate: Bearer resource_metadata="https://…"
    private static readonly Regex ResourceMetadataRe =
        new(@"resource_metadata\s*=\s*""(?<url>[^""]+)""|resource_metadata\s*=\s*(?<bare>[^\s,]+)",
            RegexOptions.IgnoreCase | RegexOptions.Compiled);

    private static readonly Regex ScopeRe =
        new(@"scope\s*=\s*""(?<s>[^""]+)""", RegexOptions.IgnoreCase | RegexOptions.Compiled);

    /// <summary>The <c>resource_metadata</c> URL from a 401's WWW-Authenticate
    /// header, or null. Only honoured on a 401 (the caller enforces the status).</summary>
    public static string? ResourceMetadataUrl(string? wwwAuthenticate)
    {
        if (string.IsNullOrWhiteSpace(wwwAuthenticate)) return null;
        var m = ResourceMetadataRe.Match(wwwAuthenticate);
        if (!m.Success) return null;
        var url = m.Groups["url"].Success ? m.Groups["url"].Value : m.Groups["bare"].Value;
        return string.IsNullOrWhiteSpace(url) ? null : url.Trim();
    }

    /// <summary>The scope hint from a 401's WWW-Authenticate header, if any.</summary>
    public static string? ScopeHint(string? wwwAuthenticate)
    {
        if (string.IsNullOrWhiteSpace(wwwAuthenticate)) return null;
        var m = ScopeRe.Match(wwwAuthenticate);
        return m.Success ? m.Groups["s"].Value.Trim() : null;
    }

    /// <summary>RFC 9728: the AS to use. Claude uses the FIRST
    /// <c>authorization_servers</c> entry and does NOT fall back to later ones.
    /// Null when the list is missing/empty.</summary>
    public static string? PrimaryAuthorizationServer(ProtectedResourceMetadata? prm) =>
        prm?.AuthorizationServers is { Count: > 0 } list ? list[0]?.Trim() : null;

    /// <summary>The RFC 8414 metadata URL for an issuer: the issuer's origin +
    /// <c>/.well-known/oauth-authorization-server</c> + the issuer's path (RFC 8414
    /// §3). Also returns the OIDC location as a fallback the caller may try.</summary>
    public static (string primary, string oidc) MetadataUrls(string issuer)
    {
        var baseUri = new Uri(issuer.TrimEnd('/') + "/");
        var path = baseUri.AbsolutePath.TrimEnd('/'); // "" for root issuers
        var origin = baseUri.GetLeftPart(UriPartial.Authority);
        var primary = $"{origin}/.well-known/oauth-authorization-server{path}";
        var oidc = $"{origin}/.well-known/openid-configuration{path}";
        return (primary, oidc);
    }

    /// <summary>The scopes to request. Precedence: the 401's WWW-Authenticate
    /// scope, else the PRM's <c>scopes_supported</c>. Then append
    /// <c>offline_access</c> IF the authorization server advertises it in its own
    /// <c>scopes_supported</c> — this is how a refresh token is obtained. Order is
    /// preserved and duplicates dropped; may be empty (a valid request).</summary>
    public static string SelectScopes(string? wwwAuthScope, ProtectedResourceMetadata? prm, AuthServerMetadata? asMeta)
    {
        var chosen = new List<string>();
        void Add(IEnumerable<string>? src)
        {
            foreach (var s in src ?? Enumerable.Empty<string>())
            {
                var t = s?.Trim();
                if (!string.IsNullOrEmpty(t) && !chosen.Contains(t)) chosen.Add(t);
            }
        }

        if (!string.IsNullOrWhiteSpace(wwwAuthScope)) Add(wwwAuthScope.Split(' ', StringSplitOptions.RemoveEmptyEntries));
        else Add(prm?.ScopesSupported);

        var asOffersOffline = asMeta?.ScopesSupported?.Any(s => string.Equals(s?.Trim(), "offline_access", StringComparison.Ordinal)) == true;
        if (asOffersOffline && !chosen.Contains("offline_access")) chosen.Add("offline_access");

        return string.Join(' ', chosen);
    }

    /// <summary>The authorization server must advertise S256 PKCE
    /// (<c>code_challenge_methods_supported: ["S256"]</c>). We proceed only when it
    /// does — a server that cannot verify S256 cannot protect the loopback code.</summary>
    public static bool SupportsS256(AuthServerMetadata? asMeta) =>
        asMeta?.CodeChallengeMethodsSupported?.Any(m => string.Equals(m?.Trim(), "S256", StringComparison.Ordinal)) == true;
}
