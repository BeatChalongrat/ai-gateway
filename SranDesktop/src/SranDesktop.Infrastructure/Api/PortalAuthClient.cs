using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Api;

/// <summary>
/// Talks to the FastAPI gateway's Portal auth endpoints (:8080/portal/auth/*).
/// The server returns tokens as HttpOnly cookies; this client reads them out of
/// the Set-Cookie headers (the HttpClient must have UseCookies = false so the
/// headers stay visible and no stale cookie jar interferes with body-based refresh).
/// </summary>
public sealed class PortalAuthClient : IPortalAuthClient
{
    internal const string AccessCookieName = "portal_access_token";
    internal const string RefreshCookieName = "portal_refresh_token";

    internal static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;

    public PortalAuthClient(HttpClient http)
    {
        _http = http;
    }

    public async Task<AuthTokenResponse> LoginAsync(string email, string password, CancellationToken ct = default)
    {
        using var response = await _http.PostAsJsonAsync(
            "portal/auth/login", new LoginRequest(email, password), JsonOptions, ct);
        return await ReadSessionAsync(response, ct);
    }

    public async Task<AuthTokenResponse> RefreshAsync(string refreshToken, CancellationToken ct = default)
    {
        using var response = await _http.PostAsJsonAsync(
            "portal/auth/refresh", new RefreshRequest(refreshToken), JsonOptions, ct);
        return await ReadSessionAsync(response, ct);
    }

    private static async Task<AuthTokenResponse> ReadSessionAsync(
        HttpResponseMessage response, CancellationToken ct)
    {
        if (!response.IsSuccessStatusCode)
        {
            var errorBody = await response.Content.ReadAsStringAsync(ct);
            throw new HttpRequestException(
                $"Portal auth request failed ({(int)response.StatusCode}): {Truncate(errorBody, 500)}",
                null,
                response.StatusCode);
        }

        // The stack can return 200 with an error envelope in the body (and no
        // cookies) for auth failures — treat "no token cookies" as a failure
        // and surface the envelope's message as a 401.
        var cookies = ParseSetCookies(response);
        var hasAccess = cookies.TryGetValue(AccessCookieName, out var accessToken) && accessToken.Length > 0;
        var hasRefresh = cookies.TryGetValue(RefreshCookieName, out var refreshToken) && refreshToken.Length > 0;

        if (!hasAccess || !hasRefresh)
        {
            var rawBody = await response.Content.ReadAsStringAsync(ct);
            var message = TryReadErrorMessage(rawBody) ?? "Portal auth response did not include token cookies";
            throw new HttpRequestException(message, null, HttpStatusCode.Unauthorized);
        }

        var body = TryDeserialize<PortalSessionBody>(await response.Content.ReadAsStringAsync(ct));
        return new AuthTokenResponse(accessToken!, refreshToken!, body?.User);
    }

    private static string? TryReadErrorMessage(string body)
    {
        try
        {
            using var doc = JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("error", out var error)
                && error.ValueKind == JsonValueKind.Object
                && error.TryGetProperty("message", out var message))
            {
                return message.GetString();
            }
        }
        catch (JsonException)
        {
        }
        return null;
    }

    private static T? TryDeserialize<T>(string body) where T : class
    {
        try
        {
            return JsonSerializer.Deserialize<T>(body, JsonOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    internal static Dictionary<string, string> ParseSetCookies(HttpResponseMessage response)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (!response.Headers.TryGetValues("Set-Cookie", out var headers))
            return result;

        foreach (var header in headers)
        {
            var pair = header.Split(';', 2)[0];
            var eq = pair.IndexOf('=');
            if (eq <= 0) continue;

            var name = pair[..eq].Trim();
            var value = pair[(eq + 1)..].Trim().Trim('"');
            result[name] = value;
        }

        return result;
    }

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max] + "...";
}
