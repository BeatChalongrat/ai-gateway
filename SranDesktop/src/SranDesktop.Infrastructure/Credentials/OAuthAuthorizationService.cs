using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;

namespace SranDesktop.Infrastructure.Credentials;

/// <summary>
/// The interactive half of OAuth: open the browser, catch the redirect, redeem
/// the code.
///
/// The order of the checks is the security. State is consumed BEFORE the code is
/// redeemed, so a replayed or forged callback never reaches the token endpoint.
/// The redirect URI given to the provider is the one this process is listening
/// on, and the same string is sent at redemption, so a provider that was asked
/// to redirect somewhere else will refuse. And the requested scopes are filtered
/// against the deployment's allow-list before the browser opens, so a connector
/// cannot widen its own grant by asking.
/// </summary>
public sealed class OAuthAuthorizationService : IOAuthAuthorizationService
{
    private readonly IConnectorCredentialStore _store;
    private readonly IOAuthTokenClient _tokens;
    private readonly IBrowserLauncher _browser;
    private readonly OAuthAttemptRegistry _attempts;
    private readonly ILogger? _logger;

    /// <summary>How long the user has to complete sign-in.</summary>
    public static TimeSpan CallbackTimeout { get; } = TimeSpan.FromMinutes(5);

    public OAuthAuthorizationService(
        IConnectorCredentialStore store,
        IOAuthTokenClient tokens,
        IBrowserLauncher browser,
        OAuthAttemptRegistry? attempts = null,
        ILogger? logger = null)
    {
        _store = store;
        _tokens = tokens;
        _browser = browser;
        _attempts = attempts ?? new OAuthAttemptRegistry();
        _logger = logger;
    }

    public async Task<OAuthConnectResult> ConnectAsync(
        OAuthProviderConfig provider,
        string tenantId,
        IReadOnlyList<string> requestedScopes,
        UserApprovalEvidence evidence,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(provider);
        // Connecting an account grants this machine standing access to somebody
        // else's data. It is a decision, and a decision needs a person behind it.
        ArgumentNullException.ThrowIfNull(evidence);

        var scopes = ResolveScopes(provider, requestedScopes, out var rejected);
        if (rejected.Count > 0)
            return OAuthConnectResult.Fail(
                "ScopeNotAllowed",
                "This connector is not permitted to request: "
                + string.Join(", ", rejected) + ".");

        using var listener = new LoopbackOAuthCallbackListener(provider.RedirectPath, _logger);
        var challenge = OAuthPkceChallenge.Create();
        _attempts.Register(
            challenge, provider.ConnectorId, tenantId, listener.RedirectUri, scopes);

        var authorizeUrl = BuildAuthorizeUrl(provider, challenge, listener.RedirectUri, scopes);
        if (!_browser.Open(authorizeUrl))
            return OAuthConnectResult.Fail(
                "BrowserUnavailable", "The sign-in page could not be opened.");

        var callback = await listener.WaitAsync(CallbackTimeout, ct);

        // State first, always. A callback whose state we did not issue — or
        // already consumed — is discarded without the code ever being used.
        if (!_attempts.TryConsume(callback.State, out var attempt) || attempt is null)
            return OAuthConnectResult.Fail(
                "StateRejected",
                "The sign-in response did not match a request this app started. "
                + "Nothing was connected.");

        if (!callback.Success)
            return OAuthConnectResult.Fail(
                callback.Error ?? "AuthorizationFailed",
                SensitiveDataRedactor.Redact(
                    callback.ErrorDescription ?? "Sign-in did not complete."));

        if (!string.Equals(attempt.RedirectUri, listener.RedirectUri, StringComparison.Ordinal))
            return OAuthConnectResult.Fail(
                "RedirectMismatch",
                "The redirect target changed during sign-in.");

        var exchange = await _tokens.ExchangeCodeAsync(
            provider, callback.Code!, attempt.Challenge.Verifier, attempt.RedirectUri, ct);
        if (!exchange.Success || string.IsNullOrEmpty(exchange.AccessToken))
            return OAuthConnectResult.Fail(
                "TokenExchangeFailed",
                exchange.Error ?? "The provider did not issue a token.");

        // What the provider granted, not what we asked for. Recording the
        // request would make every later scope check a lie.
        var granted = exchange.GrantedScopes.Count > 0 ? exchange.GrantedScopes : scopes;
        var overGranted = granted
            .Where(scope => !provider.AllowedScopes.Contains(scope, StringComparer.OrdinalIgnoreCase))
            .ToList();
        if (overGranted.Count > 0)
        {
            // A provider handing back more than was asked for is not a bonus.
            _logger?.LogWarning(
                "Connector {Connector} received scopes outside its policy: {Scopes}",
                provider.ConnectorId, string.Join(", ", overGranted));
            return OAuthConnectResult.Fail(
                "ScopeEscalation",
                "The provider granted access this connector is not permitted to hold: "
                + string.Join(", ", overGranted) + ". Nothing was stored.");
        }

        var accountId = ResolveAccountId(exchange.AccessToken!, provider.ConnectorId);
        var descriptor = await _store.SaveAsync(
            new ConnectorCredentialDescriptor
            {
                Account = new ConnectorAccountRef(provider.ConnectorId, tenantId, accountId),
                DisplayName = accountId,
                Kind = ConnectorCredentialKind.OAuth2,
                Scopes = granted,
                AccessTokenExpiresUtc = exchange.ExpiresUtc,
                State = ConnectorCredentialState.Active,
                CreatedUtc = DateTimeOffset.UtcNow
            },
            exchange.AccessToken,
            exchange.RefreshToken,
            ct);

        _logger?.LogInformation(
            "Connected {Connector} account {Account} in tenant {Tenant} with {Count} scopes",
            provider.ConnectorId, accountId, tenantId, granted.Count);
        return OAuthConnectResult.Ok(descriptor);
    }

    /// <summary>
    /// Filters requested scopes through the deployment's allow-list.
    /// </summary>
    private static IReadOnlyList<string> ResolveScopes(
        OAuthProviderConfig provider,
        IReadOnlyList<string>? requested,
        out IReadOnlyList<string> rejected)
    {
        var wanted = requested is { Count: > 0 } ? requested : provider.DefaultScopes;
        rejected = wanted
            .Where(scope =>
                !provider.AllowedScopes.Contains(scope, StringComparer.OrdinalIgnoreCase))
            .ToList();
        return wanted
            .Where(scope =>
                provider.AllowedScopes.Contains(scope, StringComparer.OrdinalIgnoreCase))
            .ToList();
    }

    internal static string BuildAuthorizeUrl(
        OAuthProviderConfig provider,
        OAuthPkceChallenge challenge,
        string redirectUri,
        IReadOnlyList<string> scopes)
    {
        var query = new List<string>
        {
            "response_type=code",
            $"client_id={Uri.EscapeDataString(provider.ClientId)}",
            $"redirect_uri={Uri.EscapeDataString(redirectUri)}",
            $"state={Uri.EscapeDataString(challenge.State)}",
            $"code_challenge={Uri.EscapeDataString(challenge.Challenge)}",
            $"code_challenge_method={challenge.ChallengeMethod}"
        };
        if (scopes.Count > 0)
            query.Add($"scope={Uri.EscapeDataString(string.Join(" ", scopes))}");
        var separator = provider.AuthorizationEndpoint.Contains('?') ? "&" : "?";
        return provider.AuthorizationEndpoint + separator + string.Join("&", query);
    }

    /// <summary>
    /// Names the account without calling the provider's userinfo endpoint.
    ///
    /// A JWT access token carries a subject; a bearer string does not. When
    /// there is nothing to read, a stable hash of the token is used — it
    /// distinguishes two accounts on the same connector, which is what the
    /// account id is FOR, without inventing an identity the provider never
    /// asserted. The token itself never reaches the descriptor either way.
    /// </summary>
    internal static string ResolveAccountId(string accessToken, string connectorId)
    {
        var parts = accessToken.Split('.');
        if (parts.Length == 3)
        {
            try
            {
                var payload = parts[1].Replace('-', '+').Replace('_', '/');
                payload = payload.PadRight(payload.Length + (4 - payload.Length % 4) % 4, '=');
                using var document = JsonDocument.Parse(Convert.FromBase64String(payload));
                foreach (var claim in new[] { "email", "preferred_username", "upn", "sub" })
                    if (document.RootElement.TryGetProperty(claim, out var value)
                        && value.ValueKind == JsonValueKind.String
                        && value.GetString() is { Length: > 0 } text)
                        return text;
            }
            catch (Exception ex) when (ex is JsonException or FormatException)
            {
            }
        }
        return $"{connectorId}-{SensitiveDataRedactor.Fingerprint(accessToken)}";
    }
}
