using System.Text.Json;
using System.Text.Json.Serialization;

namespace SranDesktop.Infrastructure.Oauth;

/// <summary>FIX7 — the wire shapes of the client-native OAuth flow. Kept minimal:
/// only the fields the flow reads. All discovery documents are JSON; the token
/// endpoint takes form-urlencoded and returns JSON; /register takes and returns
/// JSON (RFC 7591). Records so they are trivially testable from fixture JSON.</summary>
public static class OauthJson
{
    public static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web);
}

/// <summary>RFC 9728 protected-resource metadata. <c>resource</c> must match the
/// MCP server URL exactly; <c>authorization_servers[0]</c> is the AS (Claude uses
/// the FIRST entry and does not fall back).</summary>
public sealed record ProtectedResourceMetadata(
    [property: JsonPropertyName("resource")] string? Resource,
    [property: JsonPropertyName("authorization_servers")] List<string>? AuthorizationServers,
    [property: JsonPropertyName("scopes_supported")] List<string>? ScopesSupported);

/// <summary>RFC 8414 / OIDC authorization-server metadata. Only the endpoints and
/// capability flags the flow needs.</summary>
public sealed record AuthServerMetadata(
    [property: JsonPropertyName("issuer")] string? Issuer,
    [property: JsonPropertyName("authorization_endpoint")] string? AuthorizationEndpoint,
    [property: JsonPropertyName("token_endpoint")] string? TokenEndpoint,
    [property: JsonPropertyName("registration_endpoint")] string? RegistrationEndpoint,
    [property: JsonPropertyName("scopes_supported")] List<string>? ScopesSupported,
    [property: JsonPropertyName("code_challenge_methods_supported")] List<string>? CodeChallengeMethodsSupported);

/// <summary>RFC 7591 dynamic client registration RESPONSE (the fields we keep).</summary>
public sealed record ClientRegistration(
    [property: JsonPropertyName("client_id")] string? ClientId,
    [property: JsonPropertyName("client_secret")] string? ClientSecret,
    [property: JsonPropertyName("client_id_issued_at")] long? IssuedAt);

/// <summary>RFC 6749 token endpoint response (access + optional rotating refresh).</summary>
public sealed record TokenResponse(
    [property: JsonPropertyName("access_token")] string? AccessToken,
    [property: JsonPropertyName("refresh_token")] string? RefreshToken,
    [property: JsonPropertyName("token_type")] string? TokenType,
    [property: JsonPropertyName("expires_in")] long? ExpiresIn,
    [property: JsonPropertyName("scope")] string? Scope);

/// <summary>What we PERSIST per connector after a successful flow — never the
/// portal JWT, only this one third-party service's tokens. Stored encrypted
/// (DPAPI), and the tokens themselves are never logged. <c>ExpiresAtUtc</c> drives
/// proactive refresh (up to five minutes before it).</summary>
public sealed record StoredConnectorToken(
    [property: JsonPropertyName("access_token")] string AccessToken,
    [property: JsonPropertyName("refresh_token")] string? RefreshToken,
    [property: JsonPropertyName("token_endpoint")] string TokenEndpoint,
    [property: JsonPropertyName("client_id")] string ClientId,
    [property: JsonPropertyName("client_secret")] string? ClientSecret,
    [property: JsonPropertyName("scope")] string? Scope,
    [property: JsonPropertyName("expires_at_utc")] DateTimeOffset? ExpiresAtUtc)
{
    /// <summary>Proactive-refresh window: Claude refreshes up to FIVE MINUTES
    /// before the stored expiry. True when we are inside that window (or already
    /// past expiry). No expiry known → never proactively refresh (reactive only).</summary>
    public bool NeedsProactiveRefresh(DateTimeOffset now) =>
        ExpiresAtUtc is { } exp && now >= exp - TimeSpan.FromMinutes(5);

    public string ToJson() => JsonSerializer.Serialize(this, OauthJson.Options);
    public static StoredConnectorToken? FromJson(string? json) =>
        string.IsNullOrEmpty(json) ? null
        : JsonSerializer.Deserialize<StoredConnectorToken>(json, OauthJson.Options);
}
