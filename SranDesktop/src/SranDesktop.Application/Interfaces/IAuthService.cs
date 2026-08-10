using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

public interface IAuthService
{
    bool IsAuthenticated { get; }
    UserInfo? CurrentUser { get; }

    /// <summary>Login with email/password; persists tokens on success.</summary>
    Task LoginAsync(string email, string password, CancellationToken ct = default);

    /// <summary>SSO Phase 2: complete a Google sign-in by redeeming the
    /// one-time deep-link ticket + app-minted nonce; persists tokens on
    /// success (same DPAPI-backed session as password login).</summary>
    Task SignInWithTicketAsync(string ticket, string nonce, CancellationToken ct = default);

    /// <summary>Pre-auth public settings (password form visibility etc.).
    /// Callers should fail open (assume defaults) when the server is
    /// unreachable — this is UI truth, not enforcement.</summary>
    Task<PortalPublicSettings> GetPublicSettingsAsync(CancellationToken ct = default);

    /// <summary>Restore a previous session from the credential store. Returns false if none.</summary>
    Task<bool> TryRestoreSessionAsync(CancellationToken ct = default);

    Task LogoutAsync(CancellationToken ct = default);
}
