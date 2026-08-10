using System.Security.Cryptography;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Application.Services;

/// <summary>
/// SSO Phase 2 — drives the Google sign-in hand-off (server-side PKCE,
/// way B). Mirrors <see cref="ConnectorOAuthCoordinator"/>'s shape:
/// start opens the system browser, the deep link only ASSERTS an outcome,
/// and nothing is trusted until a backend round-trip proves it.
///
/// Security model:
/// * The app mints a crypto-random nonce per attempt and keeps it in
///   memory only. The backend binds the one-time ticket to that nonce.
/// * The ``sran://oauth/callback?ticket=…`` link carries ONLY the opaque
///   ticket. Any local app can register sran:// — a hijacked link without
///   the in-memory nonce cannot be redeemed (and a hijacker's attempt
///   burns the one-shot ticket, so the theft fails loudly, not silently).
/// * The re-confirm step IS the redeem call: tokens come from
///   POST /oauth/desktop/exchange over HTTPS, never from the link.
/// </summary>
public sealed class LoginOAuthCoordinator
{
    private readonly IAuthService _auth;
    private readonly IBrowserLauncher _browser;
    private readonly ISettingsService _settings;
    private readonly ILogger<LoginOAuthCoordinator> _logger;

    private readonly object _lock = new();
    private string? _pendingNonce;

    /// <summary>Raised (on a background thread) when the ticket exchange
    /// succeeded and the session is persisted.</summary>
    public event EventHandler? SignInSucceeded;

    /// <summary>Raised (on a background thread) with a coarse reason code:
    /// ``browser_failed`` | ``exchange_failed``.</summary>
    public event EventHandler<string>? SignInFailed;

    public LoginOAuthCoordinator(
        IAuthService auth,
        IBrowserLauncher browser,
        ISettingsService settings,
        ILogger<LoginOAuthCoordinator>? logger = null)
    {
        _auth = auth;
        _browser = browser;
        _settings = settings;
        _logger = logger ?? NullLogger<LoginOAuthCoordinator>.Instance;
    }

    /// <summary>True while a sign-in attempt is waiting for its deep link.</summary>
    public bool HasPendingAttempt
    {
        get { lock (_lock) return _pendingNonce is not null; }
    }

    /// <summary>Mint a nonce, remember it for this attempt, and open the
    /// system browser at the backend's OAuth start URL. Starting again
    /// replaces the previous attempt (its nonce dies with it).</summary>
    public bool StartSignIn()
    {
        var nonce = MintNonce();
        lock (_lock)
        {
            _pendingNonce = nonce;
        }

        var baseUrl = _settings.Current.GatewayBaseUrl.TrimEnd('/');
        var url = $"{baseUrl}/portal/auth/oauth/start?provider=google&client=desktop&nonce={nonce}";
        if (!_browser.Open(url))
        {
            lock (_lock)
            {
                _pendingNonce = null;
            }
            _logger.LogWarning("Google sign-in: system browser failed to open");
            SignInFailed?.Invoke(this, "browser_failed");
            return false;
        }
        _logger.LogInformation("Google sign-in started (browser opened)");
        return true;
    }

    /// <summary>Entry point for App's deep-link dispatch. Non-login links
    /// are ignored (the connector coordinator owns those); a login link
    /// with no attempt in flight is dropped — an unsolicited ticket is
    /// exactly the hijack shape the nonce exists to kill. Never throws,
    /// never blocks the dispatcher thread.</summary>
    public void HandleDeepLink(string uri)
    {
        if (!OAuthDeepLink.TryParseLoginTicket(uri, out var ticket))
            return;

        string? nonce;
        lock (_lock)
        {
            nonce = _pendingNonce;
            _pendingNonce = null; // one attempt per nonce, ever
        }
        if (nonce is null)
        {
            _logger.LogWarning("Login deep link received with no sign-in attempt in flight; ignored");
            return;
        }

        _ = Task.Run(() => CompleteAsync(ticket!, nonce));
    }

    private async Task CompleteAsync(string ticket, string nonce)
    {
        try
        {
            await _auth.SignInWithTicketAsync(ticket, nonce);
            SignInSucceeded?.Invoke(this, EventArgs.Empty);
        }
        catch (Exception ex)
        {
            // Deliberately no ticket/nonce in the log line.
            _logger.LogWarning(ex, "Google sign-in ticket exchange failed");
            SignInFailed?.Invoke(this, "exchange_failed");
        }
    }

    private static string MintNonce()
    {
        // 32 random bytes → 43-char URL-safe base64 (no padding): satisfies
        // the server's ^[A-Za-z0-9_-]{22,128}$ gate with 256-bit entropy.
        var bytes = RandomNumberGenerator.GetBytes(32);
        return Convert.ToBase64String(bytes)
            .Replace('+', '-')
            .Replace('/', '_')
            .TrimEnd('=');
    }
}
