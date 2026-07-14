using System.Net;
using System.Net.Http.Headers;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Infrastructure.Api;

/// <summary>
/// Delegating handler for chat-edge-api calls: attaches the Portal access JWT
/// as a Bearer header, and on 401 performs a single-flight refresh against the
/// gateway (via IPortalAuthClient) then retries the request once.
/// </summary>
public sealed class AuthTokenHandler : DelegatingHandler
{
    private readonly ITokenSession _session;
    private readonly IPortalAuthClient _portalAuth;
    private readonly ILogger<AuthTokenHandler> _logger;
    private static readonly SemaphoreSlim RefreshLock = new(1, 1);

    public AuthTokenHandler(
        ITokenSession session,
        IPortalAuthClient portalAuth,
        ILogger<AuthTokenHandler> logger)
    {
        _session = session;
        _portalAuth = portalAuth;
        _logger = logger;
    }

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken ct)
    {
        var accessToken = _session.AccessToken;
        if (accessToken is not null)
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", accessToken);

        var response = await base.SendAsync(request, ct);
        if (response.StatusCode != HttpStatusCode.Unauthorized)
            return response;

        var refreshed = await TryRefreshAsync(accessToken, ct);
        if (!refreshed)
            return response;

        response.Dispose();
        var retry = await CloneRequestAsync(request, ct);
        retry.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _session.AccessToken);
        return await base.SendAsync(retry, ct);
    }

    private async Task<bool> TryRefreshAsync(string? staleAccessToken, CancellationToken ct)
    {
        var refreshToken = _session.RefreshToken;
        if (refreshToken is null)
            return false;

        await RefreshLock.WaitAsync(ct);
        try
        {
            // Another request may have refreshed while we waited.
            if (_session.AccessToken is not null && _session.AccessToken != staleAccessToken)
                return true;

            var tokens = await _portalAuth.RefreshAsync(refreshToken, ct);
            _session.SetSession(tokens.AccessToken, tokens.RefreshToken, tokens.User);
            _logger.LogDebug("Access token refreshed after 401");
            return true;
        }
        catch (HttpRequestException ex) when (ex.StatusCode is HttpStatusCode.Unauthorized
                                              or HttpStatusCode.Forbidden)
        {
            _logger.LogWarning("Token refresh rejected ({Status}); clearing session", ex.StatusCode);
            _session.Clear();
            return false;
        }
        catch (HttpRequestException ex)
        {
            _logger.LogWarning(ex, "Token refresh failed (network)");
            return false;
        }
        finally
        {
            RefreshLock.Release();
        }
    }

    private static async Task<HttpRequestMessage> CloneRequestAsync(
        HttpRequestMessage original, CancellationToken ct)
    {
        var clone = new HttpRequestMessage(original.Method, original.RequestUri)
        {
            Version = original.Version,
            VersionPolicy = original.VersionPolicy
        };

        foreach (var (key, value) in original.Headers)
            clone.Headers.TryAddWithoutValidation(key, value);

        if (original.Content is not null)
        {
            // Buffer the original content so it can be resent.
            var bytes = await original.Content.ReadAsByteArrayAsync(ct);
            var content = new ByteArrayContent(bytes);
            foreach (var (key, value) in original.Content.Headers)
                content.Headers.TryAddWithoutValidation(key, value);
            clone.Content = content;
        }

        foreach (var (key, value) in original.Options)
            clone.Options.Set(new HttpRequestOptionsKey<object?>(key), value);

        return clone;
    }
}
