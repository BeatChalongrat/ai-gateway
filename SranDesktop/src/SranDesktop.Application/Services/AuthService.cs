using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

public sealed class AuthService : IAuthService
{
    private readonly IPortalAuthClient _apiClient;
    private readonly ITokenSession _session;
    private readonly ILogger<AuthService> _logger;

    public AuthService(IPortalAuthClient apiClient, ITokenSession session, ILogger<AuthService> logger)
    {
        _apiClient = apiClient;
        _session = session;
        _logger = logger;
    }

    public bool IsAuthenticated => _session.HasSession;

    public UserInfo? CurrentUser => _session.User;

    public async Task LoginAsync(string email, string password, CancellationToken ct = default)
    {
        var response = await _apiClient.LoginAsync(email, password, ct);
        _session.SetSession(response.AccessToken, response.RefreshToken, response.User);
        _logger.LogInformation("Logged in as {Email}", response.User?.Email ?? email);
    }

    public async Task SignInWithTicketAsync(string ticket, string nonce, CancellationToken ct = default)
    {
        var response = await _apiClient.ExchangeTicketAsync(ticket, nonce, ct);
        _session.SetSession(response.AccessToken, response.RefreshToken, response.User);
        // Never log the ticket/nonce — the email is enough to correlate.
        _logger.LogInformation("Signed in via Google as {Email}", response.User?.Email ?? "(unknown)");
    }

    public Task<PortalPublicSettings> GetPublicSettingsAsync(CancellationToken ct = default) =>
        _apiClient.GetPublicSettingsAsync(ct);

    public async Task<bool> TryRestoreSessionAsync(CancellationToken ct = default)
    {
        var refreshToken = _session.RefreshToken;
        if (refreshToken is null)
            return false;

        try
        {
            // Proactively refresh so we start with a fresh access token
            // and learn immediately whether the stored session is still valid.
            var response = await _apiClient.RefreshAsync(refreshToken, ct);
            _session.SetSession(response.AccessToken, response.RefreshToken, response.User);
            _logger.LogInformation("Restored session for {Email}", _session.User?.Email ?? "(unknown)");
            return true;
        }
        catch (HttpRequestException ex) when (ex.StatusCode is System.Net.HttpStatusCode.Unauthorized
                                              or System.Net.HttpStatusCode.Forbidden)
        {
            _logger.LogInformation("Stored session rejected by server; clearing");
            _session.Clear();
            return false;
        }
    }

    public Task LogoutAsync(CancellationToken ct = default)
    {
        _session.Clear();
        _logger.LogInformation("Logged out");
        return Task.CompletedTask;
    }
}
