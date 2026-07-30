using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;

namespace SranDesktop.Infrastructure.Credentials;

public sealed record OAuthTokenResult(
    bool Success,
    string? AccessToken,
    string? RefreshToken,
    DateTimeOffset? ExpiresUtc,
    IReadOnlyList<string> GrantedScopes,
    string? Error)
{
    public static OAuthTokenResult Fail(string error) =>
        new(false, null, null, null, [], error);
}

/// <summary>
/// Talks to a provider's token endpoint. Separated behind an interface so the
/// broker's refresh, expiry and revocation logic can be exercised against a
/// deterministic provider rather than a live one.
/// </summary>
public interface IOAuthTokenClient
{
    Task<OAuthTokenResult> ExchangeCodeAsync(
        OAuthProviderConfig provider,
        string code,
        string codeVerifier,
        string redirectUri,
        CancellationToken ct = default);

    Task<OAuthTokenResult> RefreshAsync(
        OAuthProviderConfig provider,
        string refreshToken,
        CancellationToken ct = default);

    Task RevokeAsync(
        OAuthProviderConfig provider,
        string? token,
        CancellationToken ct = default);
}

/// <summary>
/// HTTP implementation.
///
/// The care here is almost entirely about what does NOT escape. A token
/// endpoint's error body routinely echoes the credential that caused the error,
/// and an HttpRequestException's message can carry the request. So every failure
/// path returns a redacted string, nothing raw is logged, and the exception's own
/// text is never propagated verbatim.
/// </summary>
public sealed class OAuthTokenClient : IOAuthTokenClient
{
    private readonly HttpClient _http;
    private readonly ILogger? _logger;

    /// <summary>A token response is small. A provider — or something pretending
    /// to be one — sending megabytes is not answering the question.</summary>
    public const int MaximumResponseBytes = 256 * 1024;

    public OAuthTokenClient(HttpClient? http = null, ILogger? logger = null)
    {
        _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        _logger = logger;
    }

    public Task<OAuthTokenResult> ExchangeCodeAsync(
        OAuthProviderConfig provider,
        string code,
        string codeVerifier,
        string redirectUri,
        CancellationToken ct = default) =>
        PostAsync(provider, provider.TokenEndpoint, new Dictionary<string, string>
        {
            ["grant_type"] = "authorization_code",
            ["code"] = code,
            ["client_id"] = provider.ClientId,
            ["redirect_uri"] = redirectUri,
            ["code_verifier"] = codeVerifier
        }, ct);

    public Task<OAuthTokenResult> RefreshAsync(
        OAuthProviderConfig provider,
        string refreshToken,
        CancellationToken ct = default) =>
        PostAsync(provider, provider.TokenEndpoint, new Dictionary<string, string>
        {
            ["grant_type"] = "refresh_token",
            ["refresh_token"] = refreshToken,
            ["client_id"] = provider.ClientId
        }, ct);

    public async Task RevokeAsync(
        OAuthProviderConfig provider,
        string? token,
        CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(token) || string.IsNullOrEmpty(provider.RevocationEndpoint))
            return;
        try
        {
            using var request = new HttpRequestMessage(
                HttpMethod.Post, provider.RevocationEndpoint)
            {
                Content = new FormUrlEncodedContent(new Dictionary<string, string>
                {
                    ["token"] = token,
                    ["client_id"] = provider.ClientId
                })
            };
            using var response = await _http.SendAsync(request, ct);
            _logger?.LogInformation(
                "Revocation for {Connector} returned {Status}",
                provider.ConnectorId, (int)response.StatusCode);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                       or OperationCanceledException)
        {
            // Deliberately not rethrown with the message: it can contain the
            // token. The caller has already revoked locally.
            _logger?.LogWarning(
                "Revocation for {Connector} did not complete ({Type})",
                provider.ConnectorId, ex.GetType().Name);
        }
    }

    private async Task<OAuthTokenResult> PostAsync(
        OAuthProviderConfig provider,
        string endpoint,
        Dictionary<string, string> form,
        CancellationToken ct)
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, endpoint)
            {
                Content = new FormUrlEncodedContent(form)
            };
            request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
            using var response = await _http.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, ct);

            var body = await ReadBoundedAsync(response, ct);
            if (!response.IsSuccessStatusCode)
                return OAuthTokenResult.Fail(
                    $"The provider refused the request ({(int)response.StatusCode}): "
                    + SensitiveDataRedactor.Redact(Describe(body)));

            using var document = JsonDocument.Parse(body);
            var root = document.RootElement;
            var accessToken = Text(root, "access_token");
            if (string.IsNullOrEmpty(accessToken))
                return OAuthTokenResult.Fail(
                    "The provider's response contained no access token.");

            var expiresIn = root.TryGetProperty("expires_in", out var expiry)
                            && expiry.TryGetInt32(out var seconds)
                ? seconds
                : (int?)null;

            return new OAuthTokenResult(
                true,
                accessToken,
                Text(root, "refresh_token"),
                expiresIn is { } value ? DateTimeOffset.UtcNow.AddSeconds(value) : null,
                (Text(root, "scope") ?? "")
                    .Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries),
                null);
        }
        catch (JsonException)
        {
            return OAuthTokenResult.Fail("The provider's response was not valid JSON.");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                       or OperationCanceledException or IOException)
        {
            // The exception message is not included: for a token request it can
            // contain the request, and the request contains the credential.
            return OAuthTokenResult.Fail(
                $"The provider could not be reached ({ex.GetType().Name}).");
        }
    }

    private static async Task<string> ReadBoundedAsync(
        HttpResponseMessage response, CancellationToken ct)
    {
        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        var buffer = new byte[MaximumResponseBytes];
        var total = 0;
        while (total < buffer.Length)
        {
            var read = await stream.ReadAsync(buffer.AsMemory(total), ct);
            if (read == 0)
                break;
            total += read;
        }
        return System.Text.Encoding.UTF8.GetString(buffer, 0, total);
    }

    /// <summary>An error body is untrusted provider output shown to a user, so
    /// it is bounded and stripped of anything token-shaped before it travels.</summary>
    private static string Describe(string body)
    {
        var trimmed = body.Trim();
        return trimmed.Length <= 300 ? trimmed : trimmed[..300] + "…";
    }

    private static string? Text(JsonElement root, string name) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
}
