using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Oauth;

/// <summary>
/// FIX7 — the client-native OAuth 2.0 flow (the Claude Code model): the client
/// runs the whole dance on the user's machine against the CONNECTOR'S OWN
/// authorization server, and the resulting token is scoped to that one third-party
/// service — never the user's portal JWT. Lazy discovery (RFC 9728 → RFC 8414/OIDC)
/// → DCR (RFC 7591) → PKCE S256 authorization over an RFC 8252 loopback redirect →
/// token (form-urlencoded) → refresh (reactive on 401, proactive ≤5 min before
/// expiry). Timeouts: 10 s for discovery/registration/token, 30 s for refresh.
///
/// HttpClient is injected so the whole flow is exercised in tests against a fake
/// authorization server (a delegating handler), never a mock standing in silently
/// for a real one. Tokens and secrets are NEVER logged.
/// </summary>
public sealed class ClientNativeOauthFlow
{
    private static readonly TimeSpan ShortTimeout = TimeSpan.FromSeconds(10);   // discovery / register / token
    private static readonly TimeSpan RefreshTimeout = TimeSpan.FromSeconds(30);

    // Loopback redirect patterns the client declares (port-agnostic on the AS side).
    public static readonly string[] RedirectPatterns = { "http://localhost/callback", "http://127.0.0.1/callback" };

    private readonly HttpClient _http;
    private readonly IBrowserLauncher _browser;
    private readonly ILogger _logger;

    public ClientNativeOauthFlow(HttpClient http, IBrowserLauncher browser, ILogger logger)
    {
        _http = http;
        _browser = browser;
        _logger = logger;
    }

    public sealed record Discovery(ProtectedResourceMetadata Prm, AuthServerMetadata As, string Scopes);

    /// <summary>Lazy discovery from a 401's <c>WWW-Authenticate</c> header (RFC 9728
    /// → RFC 8414/OIDC). The caller supplies the header from an unauthenticated call
    /// to the MCP server; if it is absent we fall back to probing the origin's
    /// well-known paths. Throws with a legible reason on any failure.</summary>
    public async Task<Discovery> DiscoverAsync(string mcpServerUrl, string? wwwAuthenticate, CancellationToken ct)
    {
        var prmUrl = OauthDiscovery.ResourceMetadataUrl(wwwAuthenticate)
                     ?? await ProbeResourceMetadataAsync(mcpServerUrl, ct);
        if (prmUrl is null) throw new OauthFlowException("discovery_no_resource_metadata");

        var prm = await GetJsonAsync<ProtectedResourceMetadata>(prmUrl, ShortTimeout, ct)
                  ?? throw new OauthFlowException("discovery_prm_unreadable");
        var issuer = OauthDiscovery.PrimaryAuthorizationServer(prm)
                     ?? throw new OauthFlowException("discovery_no_authorization_server");

        var (primary, oidc) = OauthDiscovery.MetadataUrls(issuer);
        var asMeta = await GetJsonAsync<AuthServerMetadata>(primary, ShortTimeout, ct)
                     ?? await GetJsonAsync<AuthServerMetadata>(oidc, ShortTimeout, ct)
                     ?? throw new OauthFlowException("discovery_as_metadata_unreadable");
        if (string.IsNullOrEmpty(asMeta.AuthorizationEndpoint) || string.IsNullOrEmpty(asMeta.TokenEndpoint))
            throw new OauthFlowException("discovery_as_missing_endpoints");
        if (!OauthDiscovery.SupportsS256(asMeta))
            throw new OauthFlowException("as_no_s256_pkce"); // cannot protect the loopback code

        var scopes = OauthDiscovery.SelectScopes(OauthDiscovery.ScopeHint(wwwAuthenticate), prm, asMeta);
        return new Discovery(prm, asMeta, scopes);
    }

    /// <summary>RFC 7591 dynamic client registration (application/json). Only when
    /// the AS exposes a <c>registration_endpoint</c> — this round's baseline is DCR;
    /// CIMD / Anthropic-held creds are out of scope. Returns the public client_id.</summary>
    public async Task<ClientRegistration> RegisterAsync(AuthServerMetadata asMeta, CancellationToken ct)
    {
        if (string.IsNullOrEmpty(asMeta.RegistrationEndpoint))
            throw new OauthFlowException("as_no_registration_endpoint"); // (CIMD/static are not this round)

        var body = new
        {
            client_name = "SRAN Desktop",
            redirect_uris = RedirectPatterns,
            grant_types = new[] { "authorization_code", "refresh_token" },
            response_types = new[] { "code" },
            token_endpoint_auth_method = "none", // public client (loopback + PKCE)
            application_type = "native",
        };
        using var cts = Link(ct, ShortTimeout);
        using var resp = await _http.PostAsJsonAsync(asMeta.RegistrationEndpoint, body, OauthJson.Options, cts.Token);
        if (!resp.IsSuccessStatusCode)
            throw new OauthFlowException($"registration_http_{(int)resp.StatusCode}");
        var reg = await resp.Content.ReadFromJsonAsync<ClientRegistration>(OauthJson.Options, cts.Token);
        if (reg is null || string.IsNullOrEmpty(reg.ClientId))
            throw new OauthFlowException("registration_no_client_id");
        return reg;
    }

    /// <summary>Build the authorization URL (PKCE S256 + state), open the system
    /// browser at it, and capture the code on the loopback redirect. The verifier is
    /// returned for the token exchange and never leaves this process. Uses the
    /// supplied redirect_uri (from the running loopback server) exactly.</summary>
    public async Task<(string code, string verifier)> AuthorizeAsync(
        AuthServerMetadata asMeta, string clientId, string scopes, LoopbackRedirectServer loopback, CancellationToken ct)
    {
        var pkce = PkcePair.Create();
        var state = PkcePair.Base64Url(RandomNumberGenerator.GetBytes(16));

        var q = new Dictionary<string, string?>
        {
            ["response_type"] = "code",
            ["client_id"] = clientId,
            ["redirect_uri"] = loopback.RedirectUri,
            ["state"] = state,
            ["code_challenge"] = pkce.Challenge,
            ["code_challenge_method"] = PkcePair.Method,
        };
        if (!string.IsNullOrEmpty(scopes)) q["scope"] = scopes;
        var authUrl = QueryHelpers.AddQuery(asMeta.AuthorizationEndpoint!, q);

        if (!_browser.Open(authUrl))
            throw new OauthFlowException("browser_launch_failed");
        _logger.LogInformation("oauth authorize: opened system browser (redirect on loopback :{Port})", loopback.Port);

        var code = await loopback.WaitForCodeAsync(state, ct);
        return (code, pkce.Verifier);
    }

    /// <summary>Exchange the authorization code for tokens (form-urlencoded, RFC
    /// 6749 §4.1.3). Returns the persistable token record.</summary>
    public async Task<StoredConnectorToken> ExchangeAsync(
        AuthServerMetadata asMeta, ClientRegistration reg, string redirectUri, string code, string verifier, string scopes, DateTimeOffset now, CancellationToken ct)
    {
        var form = new Dictionary<string, string>
        {
            ["grant_type"] = "authorization_code",
            ["code"] = code,
            ["redirect_uri"] = redirectUri,
            ["client_id"] = reg.ClientId!,
            ["code_verifier"] = verifier,
        };
        if (!string.IsNullOrEmpty(reg.ClientSecret)) form["client_secret"] = reg.ClientSecret!;
        var tok = await PostTokenAsync(asMeta.TokenEndpoint!, form, ShortTimeout, ct);
        return ToStored(tok, asMeta.TokenEndpoint!, reg, scopes, now);
    }

    /// <summary>Refresh (reactive on 401 or proactive before expiry). 30 s budget.
    /// A dead refresh token surfaces as <c>invalid_grant</c> → the connector needs a
    /// re-connect, not a retry. Rotating refresh tokens (public client) are picked up
    /// from the response.</summary>
    public async Task<StoredConnectorToken> RefreshAsync(StoredConnectorToken stored, DateTimeOffset now, CancellationToken ct)
    {
        if (string.IsNullOrEmpty(stored.RefreshToken))
            throw new OauthFlowException("no_refresh_token"); // must re-connect
        var form = new Dictionary<string, string>
        {
            ["grant_type"] = "refresh_token",
            ["refresh_token"] = stored.RefreshToken!,
            ["client_id"] = stored.ClientId,
        };
        if (!string.IsNullOrEmpty(stored.ClientSecret)) form["client_secret"] = stored.ClientSecret!;
        var tok = await PostTokenAsync(stored.TokenEndpoint, form, RefreshTimeout, ct);
        // Keep the old refresh token if the server did not rotate (some don't).
        var next = tok with { RefreshToken = tok.RefreshToken ?? stored.RefreshToken };
        return ToStored(next, stored.TokenEndpoint, new ClientRegistration(stored.ClientId, stored.ClientSecret, null), stored.Scope ?? "", now);
    }

    // ── internals ────────────────────────────────────────────────────────────

    private async Task<TokenResponse> PostTokenAsync(string tokenEndpoint, Dictionary<string, string> form, TimeSpan timeout, CancellationToken ct)
    {
        using var cts = Link(ct, timeout);
        using var content = new FormUrlEncodedContent(form); // application/x-www-form-urlencoded
        using var resp = await _http.PostAsync(tokenEndpoint, content, cts.Token);
        var text = await resp.Content.ReadAsStringAsync(cts.Token);
        if (!resp.IsSuccessStatusCode)
        {
            // RFC 6749 §5.2 error body — surface the CODE (e.g. invalid_grant), never the body verbatim (may carry a token).
            var code = TryReadErrorCode(text) ?? $"http_{(int)resp.StatusCode}";
            throw new OauthFlowException($"token_error:{code}");
        }
        var tok = JsonSerializer.Deserialize<TokenResponse>(text, OauthJson.Options);
        if (tok is null || string.IsNullOrEmpty(tok.AccessToken))
            throw new OauthFlowException("token_no_access_token");
        return tok;
    }

    private static StoredConnectorToken ToStored(TokenResponse tok, string tokenEndpoint, ClientRegistration reg, string scopes, DateTimeOffset now) =>
        new(
            AccessToken: tok.AccessToken!,
            RefreshToken: tok.RefreshToken,
            TokenEndpoint: tokenEndpoint,
            ClientId: reg.ClientId!,
            ClientSecret: reg.ClientSecret,
            Scope: string.IsNullOrEmpty(tok.Scope) ? (string.IsNullOrEmpty(scopes) ? null : scopes) : tok.Scope,
            ExpiresAtUtc: tok.ExpiresIn is { } secs and > 0 ? now.AddSeconds(secs) : null);

    private async Task<T?> GetJsonAsync<T>(string url, TimeSpan timeout, CancellationToken ct)
    {
        try
        {
            using var cts = Link(ct, timeout);
            using var resp = await _http.GetAsync(url, cts.Token);
            if (!resp.IsSuccessStatusCode) return default;
            return await resp.Content.ReadFromJsonAsync<T>(OauthJson.Options, cts.Token);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            return default;
        }
    }

    private async Task<string?> ProbeResourceMetadataAsync(string mcpServerUrl, CancellationToken ct)
    {
        // RFC 9728 fallback: /.well-known/oauth-protected-resource/<path> then /.well-known/oauth-protected-resource
        if (!Uri.TryCreate(mcpServerUrl, UriKind.Absolute, out var u)) return null;
        var origin = u.GetLeftPart(UriPartial.Authority);
        var path = u.AbsolutePath.TrimEnd('/');
        foreach (var candidate in new[] { $"{origin}/.well-known/oauth-protected-resource{path}", $"{origin}/.well-known/oauth-protected-resource" })
        {
            var prm = await GetJsonAsync<ProtectedResourceMetadata>(candidate, ShortTimeout, ct);
            if (prm is not null) return candidate;
        }
        return null;
    }

    private static string? TryReadErrorCode(string body)
    {
        try
        {
            using var doc = JsonDocument.Parse(body);
            return doc.RootElement.TryGetProperty("error", out var e) && e.ValueKind == JsonValueKind.String ? e.GetString() : null;
        }
        catch { return null; }
    }

    private static CancellationTokenSource Link(CancellationToken ct, TimeSpan timeout)
    {
        var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(timeout);
        return cts;
    }
}

/// <summary>A flow failure carrying a PII-free, token-free CODE (never a value).</summary>
public sealed class OauthFlowException(string code) : Exception(code)
{
    public string Code => Message;
}

/// <summary>Minimal query-string builder (no ASP.NET dependency in Infrastructure).</summary>
internal static class QueryHelpers
{
    public static string AddQuery(string url, IEnumerable<KeyValuePair<string, string?>> parameters)
    {
        var sep = url.Contains('?') ? '&' : '?';
        var sb = new System.Text.StringBuilder(url);
        foreach (var (k, v) in parameters)
        {
            if (v is null) continue;
            sb.Append(sep).Append(Uri.EscapeDataString(k)).Append('=').Append(Uri.EscapeDataString(v));
            sep = '&';
        }
        return sb.ToString();
    }
}
