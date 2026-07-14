using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Auth client for the FastAPI gateway (:8080). Identity is owned by the Portal:
/// POST /portal/auth/login and /portal/auth/refresh return the user in the body
/// and the JWT pair in HttpOnly cookies, which this client extracts so the app
/// can call chat-edge-api with an Authorization: Bearer header.
/// </summary>
public interface IPortalAuthClient
{
    Task<AuthTokenResponse> LoginAsync(string email, string password, CancellationToken ct = default);

    /// <summary>Rotates the token pair. The refresh token is sent in the body (non-browser client path).</summary>
    Task<AuthTokenResponse> RefreshAsync(string refreshToken, CancellationToken ct = default);
}
