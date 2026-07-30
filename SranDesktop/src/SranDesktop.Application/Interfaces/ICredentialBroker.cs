using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Persists connector credentials. Split in two on purpose: the descriptor is
/// metadata that may live anywhere, the secret goes to OS-backed protected
/// storage and comes back only inside the store.
///
/// There is deliberately no method that returns a token as a string to an
/// arbitrary caller. The broker is the only thing that reads secrets, and it
/// hands out leases rather than values.
/// </summary>
public interface IConnectorCredentialStore
{
    IReadOnlyList<ConnectorCredentialDescriptor> List(
        string? connectorId = null, string? tenantId = null);

    ConnectorCredentialDescriptor? Get(ConnectorAccountRef account);

    /// <summary>Writes the descriptor and, separately, the secret material.
    /// <paramref name="accessToken"/> and <paramref name="refreshToken"/> go to
    /// protected storage; neither is ever written next to the descriptor.</summary>
    Task<ConnectorCredentialDescriptor> SaveAsync(
        ConnectorCredentialDescriptor descriptor,
        string? accessToken,
        string? refreshToken,
        CancellationToken ct = default);

    /// <summary>Updates metadata only — state, error, expiry. Cannot touch the
    /// secret, so a status update can never clobber a working token.</summary>
    Task<ConnectorCredentialDescriptor> UpdateAsync(
        ConnectorCredentialDescriptor descriptor,
        CancellationToken ct = default);

    Task RemoveAsync(ConnectorAccountRef account, CancellationToken ct = default);

    /// <summary>Removes every credential for a connector — what uninstall does
    /// when policy says the credentials go with it.</summary>
    Task RemoveConnectorAsync(string connectorId, CancellationToken ct = default);

    /// <summary>Reads the secret. Internal to the credential layer by
    /// convention and by the fact that nothing outside it is composed with the
    /// store; the broker is the intended caller.</summary>
    (string? AccessToken, string? RefreshToken) ReadSecret(ConnectorAccountRef account);
}

/// <summary>
/// The single place a connector credential is turned into something usable.
///
/// Everything that wants to authenticate asks for a lease. That gives one
/// choke point for the things that must always happen — checking revocation,
/// noticing expiry, refreshing once and only once, enforcing that the requested
/// scopes were actually granted, and keeping the token out of everything that
/// is not the outgoing request.
/// </summary>
public interface ICredentialBroker
{
    /// <summary>
    /// Borrows a credential for one operation. Refreshes transparently when the
    /// access token has expired and a refresh token exists; refuses — rather
    /// than guessing — when it does not.
    /// </summary>
    Task<CredentialLeaseResult> LeaseAsync(
        ConnectorAccountRef account,
        IReadOnlyList<string> requiredScopes,
        CancellationToken ct = default);

    IReadOnlyList<ConnectorCredentialDescriptor> ListAccounts(
        string connectorId, string? tenantId = null);

    /// <summary>Marks a credential unusable and best-effort tells the provider.
    /// Local revocation happens first: a provider that cannot be reached must
    /// not leave a token this machine still considers good.</summary>
    Task<ConnectorCredentialDescriptor?> RevokeAsync(
        ConnectorAccountRef account, CancellationToken ct = default);

    /// <summary>Forgets the account entirely, including the protected secret.</summary>
    Task DisconnectAsync(ConnectorAccountRef account, CancellationToken ct = default);
}

/// <summary>Result of an interactive OAuth connect.</summary>
public sealed record OAuthConnectResult(
    bool Success,
    ConnectorCredentialDescriptor? Descriptor,
    string? FailureCode,
    string? FailureReason)
{
    public static OAuthConnectResult Ok(ConnectorCredentialDescriptor descriptor) =>
        new(true, descriptor, null, null);

    public static OAuthConnectResult Fail(string code, string reason) =>
        new(false, null, code, reason);
}

/// <summary>
/// Runs the authorization-code + PKCE flow against a provider.
///
/// Separated from the broker because it is the only part that needs a browser
/// and a listening socket, and because the broker has to remain usable —
/// leasing, refreshing, revoking — in contexts where neither exists, such as a
/// scheduled run at 3am.
/// </summary>
public interface IOAuthAuthorizationService
{
    Task<OAuthConnectResult> ConnectAsync(
        OAuthProviderConfig provider,
        string tenantId,
        IReadOnlyList<string> requestedScopes,
        UserApprovalEvidence evidence,
        CancellationToken ct = default);
}
