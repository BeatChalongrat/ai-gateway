using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Credentials;

namespace SranDesktop.Tests.Credentials;

/// <summary>
/// A deterministic OAuth provider.
///
/// It enforces the checks a real provider enforces — the code must exist, be
/// unused, and be redeemed with the verifier matching the challenge it was
/// issued against — because those are exactly the properties the client-side
/// flow is claiming to uphold. A fixture that accepted anything would let a
/// broken PKCE implementation pass.
/// </summary>
public sealed class FakeOAuthProvider : IOAuthTokenClient
{
    private readonly Dictionary<string, Issued> _codes = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Refresh> _refreshTokens = new(StringComparer.Ordinal);
    private readonly HashSet<string> _revoked = new(StringComparer.Ordinal);
    private int _counter;

    /// <summary>Scopes the provider hands back. Defaults to what was asked for;
    /// set to model a provider that grants less — or more — than requested.</summary>
    public IReadOnlyList<string>? GrantedScopesOverride { get; set; }

    public TimeSpan AccessTokenLifetime { get; set; } = TimeSpan.FromHours(1);

    /// <summary>
    /// Shared with the broker under test. A provider stamping expiry from the
    /// real clock while the broker reads a controlled one would hand back tokens
    /// that are already expired the moment they arrive — and the resulting
    /// refresh storm would look like a locking bug rather than a clock mismatch.
    /// </summary>
    public Func<DateTimeOffset> Clock { get; set; } = () => DateTimeOffset.UtcNow;

    /// <summary>Makes the next refresh fail, as a provider does when consent was
    /// withdrawn from its own settings page.</summary>
    public bool RefreshFails { get; set; }

    /// <summary>Models a provider that rotates refresh tokens.</summary>
    public bool RotateRefreshTokens { get; set; } = true;

    /// <summary>Error text a hostile or careless provider might echo back — used
    /// to prove nothing token-shaped survives into stored state.</summary>
    public string? RefreshErrorBody { get; set; }

    public int ExchangeCount { get; private set; }
    public int RefreshCount { get; private set; }
    public List<string> RevokedTokens { get; } = [];

    /// <summary>Issues a code the way an authorization endpoint would, binding it
    /// to the challenge and redirect it was requested with.</summary>
    public string IssueCode(
        string challenge, string redirectUri, IReadOnlyList<string> scopes, string subject)
    {
        var code = $"code-{Interlocked.Increment(ref _counter)}";
        _codes[code] = new Issued(challenge, redirectUri, scopes, subject);
        return code;
    }

    public Task<OAuthTokenResult> ExchangeCodeAsync(
        OAuthProviderConfig provider,
        string code,
        string codeVerifier,
        string redirectUri,
        CancellationToken ct = default)
    {
        ExchangeCount++;

        // Single use. A code that survived redemption would make interception
        // worth attempting.
        if (!_codes.Remove(code, out var issued))
            return Task.FromResult(OAuthTokenResult.Fail("invalid_grant: unknown or used code"));
        if (!string.Equals(issued.RedirectUri, redirectUri, StringComparison.Ordinal))
            return Task.FromResult(OAuthTokenResult.Fail("invalid_grant: redirect_uri mismatch"));
        if (!OAuthPkceChallenge.Matches(codeVerifier, issued.Challenge))
            return Task.FromResult(OAuthTokenResult.Fail("invalid_grant: PKCE verifier mismatch"));

        var scopes = GrantedScopesOverride ?? issued.Scopes;
        var refresh = $"refresh-{issued.Subject}-{Interlocked.Increment(ref _counter)}";
        _refreshTokens[refresh] = new Refresh(issued.Subject, scopes);
        return Task.FromResult(new OAuthTokenResult(
            true,
            AccessTokenFor(issued.Subject),
            refresh,
            Clock().Add(AccessTokenLifetime),
            scopes,
            null));
    }

    public Task<OAuthTokenResult> RefreshAsync(
        OAuthProviderConfig provider,
        string refreshToken,
        CancellationToken ct = default)
    {
        RefreshCount++;
        if (_revoked.Contains(refreshToken))
            return Task.FromResult(OAuthTokenResult.Fail("invalid_grant: token revoked"));
        if (RefreshFails)
            return Task.FromResult(OAuthTokenResult.Fail(
                RefreshErrorBody ?? "invalid_grant: consent withdrawn"));
        if (!_refreshTokens.TryGetValue(refreshToken, out var state))
            return Task.FromResult(OAuthTokenResult.Fail("invalid_grant: unknown refresh token"));

        string? rotated = null;
        if (RotateRefreshTokens)
        {
            _refreshTokens.Remove(refreshToken);
            rotated = $"refresh-{state.Subject}-{Interlocked.Increment(ref _counter)}";
            _refreshTokens[rotated] = state;
        }

        return Task.FromResult(new OAuthTokenResult(
            true,
            AccessTokenFor(state.Subject),
            rotated,
            Clock().Add(AccessTokenLifetime),
            GrantedScopesOverride ?? state.Scopes,
            null));
    }

    public Task RevokeAsync(
        OAuthProviderConfig provider, string? token, CancellationToken ct = default)
    {
        if (token is { Length: > 0 })
        {
            RevokedTokens.Add(token);
            _revoked.Add(token);
            _refreshTokens.Remove(token);
        }
        return Task.CompletedTask;
    }

    /// <summary>
    /// Teaches the provider about a refresh token that was issued outside this
    /// fixture — for tests that seed the store directly rather than driving the
    /// whole authorization flow. Without it the provider would rightly reject an
    /// unknown token and the test would be measuring the seeding, not the refresh.
    /// </summary>
    public void Seed(string refreshToken, string subject, IReadOnlyList<string> scopes) =>
        _refreshTokens[refreshToken] = new Refresh(subject, scopes);

    /// <summary>Marks a refresh token dead the way a provider does when the user
    /// revokes from the provider's own settings, without telling us.</summary>
    public void RevokeServerSide(string refreshToken)
    {
        _revoked.Add(refreshToken);
        _refreshTokens.Remove(refreshToken);
    }

    /// <summary>A JWT-shaped access token whose subject the account resolver can
    /// read, so two accounts on one connector are distinguishable.</summary>
    public string AccessTokenFor(string subject)
    {
        var header = Base64Url("""{"alg":"none","typ":"JWT"}"""u8.ToArray());
        var payload = Base64Url(
            System.Text.Encoding.UTF8.GetBytes(
                $$"""{"sub":"{{subject}}","email":"{{subject}}"}"""));
        return $"{header}.{payload}.sig-{Interlocked.Increment(ref _counter)}";
    }

    private static string Base64Url(byte[] bytes) =>
        Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private sealed record Issued(
        string Challenge, string RedirectUri, IReadOnlyList<string> Scopes, string Subject);

    private sealed record Refresh(string Subject, IReadOnlyList<string> Scopes);
}

/// <summary>In-memory secret store standing in for DPAPI, which is unavailable
/// to a test running as another user. The store under test is the connector
/// credential store; this is only the encrypted bucket beneath it.</summary>
public sealed class InMemoryCredentialStore : Application.Interfaces.ICredentialStore
{
    private readonly Dictionary<string, string> _values = new(StringComparer.Ordinal);

    public IReadOnlyDictionary<string, string> Values => _values;

    public void Save(string key, string value) => _values[key] = value;

    public string? Load(string key) => _values.GetValueOrDefault(key);

    public void Delete(string key) => _values.Remove(key);
}
