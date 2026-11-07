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
/// Body of /portal/auth/login, /portal/auth/refresh, and
/// /portal/auth/oauth/desktop/exchange. Login sends the tokens in
/// Set-Cookie headers only; refresh and the desktop OAuth exchange also
/// (or exclusively) put them in the JSON body — the client accepts
/// either source (SSO Phase 2 B3).
/// </summary>
public sealed record PortalSessionBody(
    [property: JsonPropertyName("user")] UserInfo? User,
    [property: JsonPropertyName("access_token")] string? AccessToken = null,
    [property: JsonPropertyName("refresh_token")] string? RefreshToken = null);

/// <summary>Body of POST /portal/auth/oauth/desktop/exchange (SSO Phase 2):
/// the one-time ticket from the sran:// deep link plus the nonce this app
/// minted before opening the browser (the server binds them).</summary>
public sealed record DesktopTicketExchangeRequest(
    [property: JsonPropertyName("ticket")] string Ticket,
    [property: JsonPropertyName("nonce")] string Nonce);

/// <summary>Composite result assembled by PortalAuthClient (body user + cookie tokens).</summary>
public sealed record AuthTokenResponse(string AccessToken, string RefreshToken, UserInfo? User);

/// <summary>Subset of GET /portal/auth/public-settings the desktop cares
/// about. ``password_login_enabled`` is UI truth for hiding the password
/// form (Phase 3 flips it server-side; enforcement is the 403 on
/// /login + /signup, not this flag).</summary>
public sealed record PortalPublicSettings(
    [property: JsonPropertyName("password_login_enabled")] bool PasswordLoginEnabled = true);
