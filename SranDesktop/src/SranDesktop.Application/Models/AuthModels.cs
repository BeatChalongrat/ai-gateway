using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

public sealed record LoginRequest(
    [property: JsonPropertyName("email")] string Email,
    [property: JsonPropertyName("password")] string Password);

public sealed record RefreshRequest(
    [property: JsonPropertyName("refresh_token")] string RefreshToken);

public sealed record UserInfo(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("email")] string Email,
    [property: JsonPropertyName("display_name")] string? DisplayName);

/// <summary>
/// Body of /portal/auth/login and /portal/auth/refresh: the tokens travel in
/// Set-Cookie headers (portal_access_token / portal_refresh_token), only the
/// user object is in the JSON body.
/// </summary>
public sealed record PortalSessionBody(
    [property: JsonPropertyName("user")] UserInfo? User);

/// <summary>Composite result assembled by PortalAuthClient (body user + cookie tokens).</summary>
public sealed record AuthTokenResponse(string AccessToken, string RefreshToken, UserInfo? User);
