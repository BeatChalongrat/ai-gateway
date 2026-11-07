using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Holds the current auth session in memory and persists it to the credential store.
/// Shared by AuthService (writes on login/logout) and the HTTP auth handler
/// (reads the access token, rotates tokens after a 401-triggered refresh).
/// </summary>
public interface ITokenSession
{
    string? AccessToken { get; }
    string? RefreshToken { get; }
    UserInfo? User { get; }
    bool HasSession { get; }

    void SetSession(string accessToken, string refreshToken, UserInfo? user);
    void Clear();

    event EventHandler? SessionChanged;
}
