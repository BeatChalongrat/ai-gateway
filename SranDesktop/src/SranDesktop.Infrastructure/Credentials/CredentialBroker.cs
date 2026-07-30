using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;

namespace SranDesktop.Infrastructure.Credentials;

/// <summary>
/// The one place a stored credential becomes a usable header.
///
/// Having exactly one means the things that must always happen have exactly one
/// place to happen in: revocation is checked, expiry is noticed, a refresh is
/// attempted once and its failure is recorded rather than retried, and the
/// granted scopes are compared against what the caller says it needs. Spread
/// across call sites, each of those is a rule somebody eventually forgets.
///
/// The lease it hands back carries a header value and nothing else. No caller
/// receives the token as a value it could log, persist, or hand to a worker.
/// </summary>
public sealed class CredentialBroker : ICredentialBroker
{
    private readonly IConnectorCredentialStore _store;
    private readonly IOAuthTokenClient _tokens;
    private readonly Func<string, OAuthProviderConfig?> _providers;
    private readonly ILogger? _logger;
    private readonly Func<DateTimeOffset> _clock;

    /// <summary>Serialises refresh per account. Two operations noticing the same
    /// expired token at once would otherwise both refresh, and providers that
    /// rotate refresh tokens invalidate the loser — turning a working account
    /// into a broken one under concurrency.</summary>
    private readonly Dictionary<string, SemaphoreSlim> _refreshLocks = new(StringComparer.Ordinal);

    public CredentialBroker(
        IConnectorCredentialStore store,
        IOAuthTokenClient tokens,
        Func<string, OAuthProviderConfig?> providers,
        ILogger? logger = null,
        Func<DateTimeOffset>? clock = null)
    {
        _store = store;
        _tokens = tokens;
        _providers = providers;
        _logger = logger;
        _clock = clock ?? (() => DateTimeOffset.UtcNow);
    }

    public IReadOnlyList<ConnectorCredentialDescriptor> ListAccounts(
        string connectorId, string? tenantId = null) => _store.List(connectorId, tenantId);

    public async Task<CredentialLeaseResult> LeaseAsync(
        ConnectorAccountRef account,
        IReadOnlyList<string> requiredScopes,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(account);
        requiredScopes ??= [];

        var descriptor = _store.Get(account);
        if (descriptor is null)
            return CredentialLeaseResult.Denied(
                CredentialLeaseFailure.NoAccount,
                $"No account is connected for {account.ConnectorId} in {account.TenantId}.");

        // A tenant mismatch is checked even though the key contains the tenant:
        // the caller's belief about which organisation it is acting for is the
        // thing being validated, and a key lookup cannot disagree with itself.
        if (!string.Equals(descriptor.Account.TenantId, account.TenantId, StringComparison.Ordinal))
            return CredentialLeaseResult.Denied(
                CredentialLeaseFailure.TenantMismatch,
                "The stored account belongs to a different tenant.", descriptor);

        if (descriptor.State == ConnectorCredentialState.Revoked)
            return CredentialLeaseResult.Denied(
                CredentialLeaseFailure.Revoked,
                "This account's access was revoked. Reconnect it to use it again.",
                descriptor);

        if (descriptor.State == ConnectorCredentialState.RefreshFailed)
            return CredentialLeaseResult.Denied(
                CredentialLeaseFailure.RefreshFailed,
                descriptor.LastError
                ?? "Renewing this account's access failed. Reconnect it.",
                descriptor);

        if (!descriptor.CoversScopes(requiredScopes))
            // Asking anyway would either fail at the provider or, worse, succeed
            // because the provider is lenient — and then the granted scope set
            // recorded here would be a lie.
            return CredentialLeaseResult.Denied(
                CredentialLeaseFailure.ScopeNotGranted,
                "This account did not grant "
                + string.Join(", ", requiredScopes.Where(scope =>
                    !descriptor.Scopes.Contains(scope, StringComparer.OrdinalIgnoreCase)))
                + ". Reconnect it and approve the additional access.",
                descriptor);

        var secret = _store.ReadSecret(account);
        if (descriptor.Kind == ConnectorCredentialKind.OAuth2
            && descriptor.IsExpired(_clock()))
        {
            var refreshed = await RefreshAsync(descriptor, secret.RefreshToken, ct);
            if (!refreshed.Granted)
                return refreshed;
            descriptor = refreshed.Descriptor!;
            secret = _store.ReadSecret(account);
        }

        if (string.IsNullOrEmpty(secret.AccessToken))
            return CredentialLeaseResult.Denied(
                CredentialLeaseFailure.NoAccount,
                "The account has no usable credential stored. Reconnect it.",
                descriptor);

        var provider = _providers(account.ConnectorId);
        return CredentialLeaseResult.Ok(
            new ScopedCredentialLease(
                descriptor.Account,
                descriptor.Scopes,
                descriptor.AccessTokenExpiresUtc,
                provider?.HeaderName ?? "Authorization",
                Compose(provider?.TokenPrefix ?? "Bearer", secret.AccessToken!)),
            descriptor);
    }

    private static string Compose(string prefix, string token) =>
        string.IsNullOrEmpty(prefix) ? token : $"{prefix} {token}";

    private async Task<CredentialLeaseResult> RefreshAsync(
        ConnectorCredentialDescriptor descriptor,
        string? refreshToken,
        CancellationToken ct)
    {
        if (string.IsNullOrEmpty(refreshToken))
        {
            var expired = await _store.UpdateAsync(descriptor with
            {
                State = ConnectorCredentialState.Expired,
                LastError = "The access token expired and there is no refresh token."
            }, ct);
            return CredentialLeaseResult.Denied(
                CredentialLeaseFailure.Expired,
                "This account's access expired. Reconnect it.", expired);
        }

        if (_providers(descriptor.Account.ConnectorId) is not { } provider)
            return CredentialLeaseResult.Denied(
                CredentialLeaseFailure.StoreUnavailable,
                "No OAuth configuration is available for this connector.", descriptor);

        var gate = LockFor(descriptor.Account);
        await gate.WaitAsync(ct);
        try
        {
            // Another operation may have refreshed while we queued. Re-read
            // before spending the refresh token, which some providers rotate.
            var current = _store.Get(descriptor.Account) ?? descriptor;
            if (!current.IsExpired(_clock())
                && current.State == ConnectorCredentialState.Active)
                return CredentialLeaseResult.Ok(
                    new ScopedCredentialLease(
                        current.Account, current.Scopes, current.AccessTokenExpiresUtc,
                        provider.HeaderName,
                        Compose(provider.TokenPrefix,
                            _store.ReadSecret(current.Account).AccessToken ?? "")),
                    current);

            var result = await _tokens.RefreshAsync(provider, refreshToken, ct);
            if (!result.Success)
            {
                // Recorded, not retried. A provider that refused a refresh will
                // refuse it again, and a loop here would burn the token and the
                // rate limit together.
                var failed = await _store.UpdateAsync(current with
                {
                    State = ConnectorCredentialState.RefreshFailed,
                    LastError = SensitiveDataRedactor.Redact(result.Error)
                }, ct);
                _logger?.LogWarning(
                    "Refresh failed for {Account}: {Error}",
                    descriptor.Account.Key, result.Error);
                return CredentialLeaseResult.Denied(
                    CredentialLeaseFailure.RefreshFailed,
                    "Renewing this account's access failed. Reconnect it.", failed);
            }

            var granted = result.GrantedScopes.Count > 0 ? result.GrantedScopes : current.Scopes;
            var updated = await _store.SaveAsync(
                current with
                {
                    State = ConnectorCredentialState.Active,
                    AccessTokenExpiresUtc = result.ExpiresUtc,
                    Scopes = granted,
                    LastError = null
                },
                result.AccessToken,
                // A provider that rotates the refresh token sends a new one;
                // one that does not expects the old one to keep working.
                result.RefreshToken ?? refreshToken,
                ct);

            return CredentialLeaseResult.Ok(
                new ScopedCredentialLease(
                    updated.Account, updated.Scopes, updated.AccessTokenExpiresUtc,
                    provider.HeaderName, Compose(provider.TokenPrefix, result.AccessToken!)),
                updated);
        }
        finally
        {
            gate.Release();
        }
    }

    public async Task<ConnectorCredentialDescriptor?> RevokeAsync(
        ConnectorAccountRef account, CancellationToken ct = default)
    {
        if (_store.Get(account) is not { } descriptor)
            return null;

        // Local first. If the provider call fails, this machine must still
        // consider the credential dead — the opposite order leaves a token that
        // works here because a network call did not.
        var revoked = await _store.UpdateAsync(descriptor with
        {
            State = ConnectorCredentialState.Revoked,
            RevokedUtc = _clock(),
            LastError = null
        }, ct);

        if (_providers(account.ConnectorId) is { } provider)
        {
            var secret = _store.ReadSecret(account);
            try
            {
                await _tokens.RevokeAsync(
                    provider, secret.RefreshToken ?? secret.AccessToken, ct);
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                           or OperationCanceledException)
            {
                _logger?.LogWarning(ex,
                    "Provider-side revocation for {Account} did not complete", account.Key);
            }
        }

        // Drop the secret regardless: a revoked credential has no use that would
        // justify keeping the material around.
        await _store.SaveAsync(revoked, null, null, ct);
        return revoked;
    }

    public async Task DisconnectAsync(ConnectorAccountRef account, CancellationToken ct = default)
    {
        await RevokeAsync(account, ct);
        await _store.RemoveAsync(account, ct);
    }

    private SemaphoreSlim LockFor(ConnectorAccountRef account)
    {
        lock (_refreshLocks)
        {
            if (!_refreshLocks.TryGetValue(account.Key, out var gate))
                _refreshLocks[account.Key] = gate = new SemaphoreSlim(1, 1);
            return gate;
        }
    }

    /// <summary>
    /// A borrowed credential. <see cref="ToString"/> is overridden because the
    /// default would print the header value into any log line that interpolates
    /// the lease — which is precisely the accident this type exists to prevent.
    /// </summary>
    private sealed class ScopedCredentialLease : IScopedCredentialLease
    {
        private string? _headerValue;

        public ScopedCredentialLease(
            ConnectorAccountRef account,
            IReadOnlyList<string> scopes,
            DateTimeOffset? expiresUtc,
            string headerName,
            string headerValue)
        {
            Account = account;
            Scopes = scopes;
            ExpiresUtc = expiresUtc;
            HeaderName = headerName;
            _headerValue = headerValue;
        }

        public ConnectorAccountRef Account { get; }
        public IReadOnlyList<string> Scopes { get; }
        public DateTimeOffset? ExpiresUtc { get; }
        public string HeaderName { get; }

        public string HeaderValue => _headerValue
                                     ?? throw new ObjectDisposedException(
                                         nameof(ScopedCredentialLease),
                                         "The credential lease was already released.");

        public void Dispose() => _headerValue = null;

        public override string ToString() =>
            $"credential lease for {Account.Key} ({Scopes.Count} scopes, redacted)";
    }
}
