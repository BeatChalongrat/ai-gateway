using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

public interface IAuthService
{
    bool IsAuthenticated { get; }
    UserInfo? CurrentUser { get; }

    /// <summary>Login with email/password; persists tokens on success.</summary>
    Task LoginAsync(string email, string password, CancellationToken ct = default);

    /// <summary>Restore a previous session from the credential store. Returns false if none.</summary>
    Task<bool> TryRestoreSessionAsync(CancellationToken ct = default);

    Task LogoutAsync(CancellationToken ct = default);
}
