using System.Security.Cryptography;
using System.Text;

namespace SranDesktop.Application.Services;

/// <summary>
/// One authorization attempt's secrets: the PKCE verifier and the state.
///
/// Both exist for the same reason and neither replaces the other. State proves
/// the callback belongs to a request THIS process started, so a link fired by
/// anything else on the machine is discarded. The verifier proves the code is
/// being redeemed by the process that asked for it, so a code stolen out of the
/// browser, the URL bar, or a proxy log cannot be exchanged by whoever has it.
/// </summary>
public sealed record OAuthPkceChallenge
{
    private OAuthPkceChallenge(string verifier, string challenge, string state)
    {
        Verifier = verifier;
        Challenge = challenge;
        State = state;
        CreatedUtc = DateTimeOffset.UtcNow;
    }

    /// <summary>Never sent to the authorization endpoint — only to the token
    /// endpoint, at redemption.</summary>
    public string Verifier { get; }

    /// <summary>S256 of the verifier. This is what travels in the browser.</summary>
    public string Challenge { get; }

    public string ChallengeMethod => "S256";

    public string State { get; }

    public DateTimeOffset CreatedUtc { get; }

    /// <summary>How long a started authorization stays redeemable. Long enough
    /// for a person to log in and grant consent, short enough that a state value
    /// left in a browser history is not usable tomorrow.</summary>
    public static TimeSpan Lifetime { get; } = TimeSpan.FromMinutes(10);

    public bool IsExpired(DateTimeOffset now) => now - CreatedUtc > Lifetime;

    public static OAuthPkceChallenge Create()
    {
        // 64 bytes → 86 base64url chars, comfortably inside the spec's 43..128.
        var verifier = Base64Url(RandomNumberGenerator.GetBytes(64));
        var challenge = Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)));
        var state = Base64Url(RandomNumberGenerator.GetBytes(32));
        return new OAuthPkceChallenge(verifier, challenge, state);
    }

    /// <summary>Verifies a verifier against a challenge — the check the provider
    /// performs, mirrored here so the fixture and the tests agree on it.</summary>
    public static bool Matches(string verifier, string challenge) =>
        CryptographicOperations.FixedTimeEquals(
            Encoding.ASCII.GetBytes(Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)))),
            Encoding.ASCII.GetBytes(challenge));

    private static string Base64Url(byte[] bytes) =>
        Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}

/// <summary>
/// Authorization attempts that are still open.
///
/// A state value is single-use: it is removed the moment it is consumed, so a
/// replayed callback finds nothing and is refused. Expiry is checked on the way
/// out rather than swept on a timer, because a sweep that has not run yet is a
/// window in which an old state still works.
/// </summary>
public sealed class OAuthAttemptRegistry
{
    private readonly object _gate = new();
    private readonly Dictionary<string, Attempt> _attempts = new(StringComparer.Ordinal);
    private readonly Func<DateTimeOffset> _clock;

    public OAuthAttemptRegistry(Func<DateTimeOffset>? clock = null) =>
        _clock = clock ?? (() => DateTimeOffset.UtcNow);

    public void Register(
        OAuthPkceChallenge challenge,
        string connectorId,
        string tenantId,
        string redirectUri,
        IReadOnlyList<string> scopes)
    {
        lock (_gate)
        {
            Prune();
            _attempts[challenge.State] =
                new Attempt(challenge, connectorId, tenantId, redirectUri, scopes);
        }
    }

    /// <summary>
    /// Consumes a state. Returns false for a state that was never issued, was
    /// already used, or has aged out — three different mistakes with the same
    /// correct response.
    /// </summary>
    public bool TryConsume(string? state, out Attempt? attempt)
    {
        attempt = null;
        if (string.IsNullOrEmpty(state))
            return false;
        lock (_gate)
        {
            Prune();
            if (!_attempts.Remove(state, out var found))
                return false;
            if (found.Challenge.IsExpired(_clock()))
                return false;
            attempt = found;
            return true;
        }
    }

    public int OpenCount
    {
        get { lock (_gate) { Prune(); return _attempts.Count; } }
    }

    private void Prune()
    {
        var now = _clock();
        foreach (var key in _attempts
                     .Where(pair => pair.Value.Challenge.IsExpired(now))
                     .Select(pair => pair.Key)
                     .ToList())
            _attempts.Remove(key);
    }

    public sealed record Attempt(
        OAuthPkceChallenge Challenge,
        string ConnectorId,
        string TenantId,
        string RedirectUri,
        IReadOnlyList<string> Scopes);
}
