using System.Net.Http;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;

namespace SranDesktop.Shell.Hosting;

public enum ShellSessionState
{
    SigningIn,
    SignedIn,
    SignedOut,
}

public enum RefreshOutcome
{
    Success,
    NetworkError,
    AuthRejected,
}

/// <summary>
/// Phase 2 (DESKTOP_SHELL_PLAN.md §2.2): the host owns the session.
///
/// Boot order is inverted relative to Phase 1: authenticate FIRST (DPAPI
/// restore, else the <c>sran://</c> system-browser flow), write the cookies
/// into the shell's WebView2 jar, and only then navigate. The page is never
/// logged out, so it never redirects, so the navigation lock is never hit in
/// normal operation.
///
/// One holder rotates: this controller refreshes on a timer strictly shorter
/// than the access TTL and re-writes the jar on every rotation. The page's own
/// refresh machinery is disabled by hosted mode (NEXT_PUBLIC_HOSTED=1), so the
/// spike's finding-7 collision (two holders, the native one losing to
/// 401 refresh_reuse) is structurally impossible rather than merely unlikely.
///
/// A refused navigation towards the Portal origin is not an error — it is the
/// page saying "I have no session". <see cref="NotifyPortalRedirectAsync"/>
/// answers it host-side: refresh; failing that, the interactive sran:// flow;
/// then re-inject and reload. The allow-list stays narrow.
/// </summary>
public sealed class ShellSessionController : IDisposable
{
    private readonly ShellOptions _options;
    private readonly IAuthService _auth;
    private readonly ITokenSession _session;
    private readonly IPortalAuthClient _portalAuth;
    private readonly LoginOAuthCoordinator _login;
    private readonly ILogger<ShellSessionController> _logger;

    private readonly SemaphoreSlim _refreshLock = new(1, 1);
    private readonly CancellationTokenSource _lifetime = new();
    private readonly object _rotationGate = new();
    private readonly Queue<DateTime> _recentRedirects = new();

    private ISessionSurface? _surface;
    private CancellationTokenSource? _rotationCts;
    private int _rotations;
    private int _reauthInFlight;

    public ShellSessionController(
        ShellOptions options,
        IAuthService auth,
        ITokenSession session,
        IPortalAuthClient portalAuth,
        LoginOAuthCoordinator login,
        ILogger<ShellSessionController> logger)
    {
        _options = options;
        _auth = auth;
        _session = session;
        _portalAuth = portalAuth;
        _login = login;
        _logger = logger;
    }

    /// <summary>Raised (from arbitrary threads) when the session state
    /// changes; the window marshals to its dispatcher.</summary>
    public event Action<ShellSessionState, string?>? StateChanged;

    /// <summary>Rotations completed by THIS controller since start — the
    /// number the Phase 2 soak criterion asks to be reported.</summary>
    public int RotationsObserved => Volatile.Read(ref _rotations);

    public bool HasSession => _session.HasSession;

    public void AttachSurface(ISessionSurface surface) => _surface = surface;

    // ------------------------------------------------------------------
    // Boot: session before navigation
    // ------------------------------------------------------------------

    /// <summary>Step 1+2 of the Phase 2 boot order. Returns true when a
    /// session exists and is server-verified (restore refreshes proactively,
    /// so success here means the server accepted us moments ago).</summary>
    public async Task<bool> EnsureSessionAtBootAsync(CancellationToken ct = default)
    {
        SetState(ShellSessionState.SigningIn, "boot");
        try
        {
            if (await _auth.TryRestoreSessionAsync(ct))
            {
                _logger.LogInformation(
                    "BOOT: stored session restored from DPAPI (server-verified via proactive refresh)");
                return true;
            }
        }
        catch (HttpRequestException ex)
        {
            // Network-down at boot: the stored session may still be good, but
            // we cannot prove it. Fall through to interactive — the user can
            // cancel and retry from the signed-out view once the network is up.
            _logger.LogWarning(ex, "BOOT: session restore failed on a network error");
        }

        _logger.LogInformation("BOOT: no stored session — starting the sran:// system-browser sign-in");
        return await InteractiveSignInAsync(ct);
    }

    /// <summary>The existing sran:// flow (LoginOAuthCoordinator), wrapped so
    /// the boot path can await its outcome. The browser opens; the deep-link
    /// ticket arrives via the single-instance pipe; the coordinator exchanges
    /// it and persists the session to DPAPI.</summary>
    public async Task<bool> InteractiveSignInAsync(CancellationToken ct = default)
    {
        var outcome = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        void OnSucceeded(object? s, EventArgs e) => outcome.TrySetResult(true);
        void OnFailed(object? s, string reason)
        {
            _logger.LogWarning("Sign-in failed: {Reason}", reason);
            outcome.TrySetResult(false);
        }

        _login.SignInSucceeded += OnSucceeded;
        _login.SignInFailed += OnFailed;
        try
        {
            if (!_login.StartSignIn())
                return false;

            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct, _lifetime.Token);
            timeout.CancelAfter(TimeSpan.FromSeconds(Math.Max(30, _options.SignInTimeoutSeconds)));
            var done = await Task.WhenAny(outcome.Task, Task.Delay(Timeout.Infinite, timeout.Token));
            if (done != outcome.Task)
            {
                _logger.LogWarning("Sign-in timed out after {Seconds}s waiting for the sran:// ticket",
                    _options.SignInTimeoutSeconds);
                return false;
            }
            return await outcome.Task;
        }
        catch (OperationCanceledException)
        {
            return false;
        }
        finally
        {
            _login.SignInSucceeded -= OnSucceeded;
            _login.SignInFailed -= OnFailed;
        }
    }

    /// <summary>Step 3: write the current DPAPI session into the WebView jar.</summary>
    public async Task ApplySessionToBrowserAsync()
    {
        var surface = _surface ?? throw new InvalidOperationException("No browser surface attached.");
        var access = _session.AccessToken;
        var refresh = _session.RefreshToken;
        if (access is null || refresh is null)
            throw new InvalidOperationException("ApplySessionToBrowserAsync called with no session.");
        await surface.WriteSessionCookiesAsync(access, refresh);
    }

    // ------------------------------------------------------------------
    // §2.4 · the host refreshes
    // ------------------------------------------------------------------

    /// <summary>(Re)start the rotation loop. Idempotent in effect: any prior
    /// loop is cancelled first, so exactly one loop runs per controller.</summary>
    public void StartRotation()
    {
        lock (_rotationGate)
        {
            _rotationCts?.Cancel();
            _rotationCts = CancellationTokenSource.CreateLinkedTokenSource(_lifetime.Token);
            var ct = _rotationCts.Token;
            _ = Task.Run(() => RotationLoopAsync(ct), CancellationToken.None);
        }
        _logger.LogInformation("Rotation timer started: every {Minutes} min (access TTL 60 min; see ShellOptions.SessionRefreshMinutes)",
            _options.SessionRefreshMinutes);
    }

    public void StopRotation()
    {
        lock (_rotationGate)
        {
            _rotationCts?.Cancel();
            _rotationCts = null;
        }
    }

    private async Task RotationLoopAsync(CancellationToken ct)
    {
        var interval = TimeSpan.FromMinutes(Math.Max(1, _options.SessionRefreshMinutes));
        var delay = interval;
        try
        {
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(delay, ct);
                var outcome = await RefreshOnceAsync("timer", ct);
                switch (outcome)
                {
                    case RefreshOutcome.Success:
                        delay = interval;
                        break;
                    case RefreshOutcome.NetworkError:
                        // The access token is still valid for the remainder of
                        // its TTL; retry soon rather than waiting a full
                        // interval on a transient outage.
                        delay = TimeSpan.FromMinutes(1);
                        _logger.LogWarning("Rotation deferred by a network error; retrying in 1 min");
                        break;
                    case RefreshOutcome.AuthRejected:
                        // Server-side invalidation discovered by the timer.
                        // Deliberately NOT the interactive flow here — popping
                        // a browser with no user gesture is intrusive. Land on
                        // the signed-out view; its button re-runs sran://.
                        await HandleSessionLostAsync("refresh_rejected");
                        return;
                }
            }
        }
        catch (OperationCanceledException)
        {
            // StopRotation or shutdown.
        }
    }

    /// <summary>One refresh + jar re-write, single-flight. Never logs a token;
    /// the jar write logs only a SHA-256/8 fingerprint (in ShellWindow).</summary>
    public async Task<RefreshOutcome> RefreshOnceAsync(string reason, CancellationToken ct = default)
    {
        await _refreshLock.WaitAsync(ct);
        try
        {
            var refreshToken = _session.RefreshToken;
            if (refreshToken is null)
                return RefreshOutcome.AuthRejected;

            var tokens = await _portalAuth.RefreshAsync(refreshToken, ct);
            _session.SetSession(tokens.AccessToken, tokens.RefreshToken, tokens.User);
            var n = Interlocked.Increment(ref _rotations);
            await ApplySessionToBrowserAsync();
            _logger.LogInformation("SESSION ROTATED #{N} (reason={Reason}) — new access+refresh written to DPAPI and the WebView jar", n, reason);
            return RefreshOutcome.Success;
        }
        catch (HttpRequestException ex) when (ex.StatusCode is System.Net.HttpStatusCode.Unauthorized
                                              or System.Net.HttpStatusCode.Forbidden)
        {
            _logger.LogWarning("SESSION REFRESH REJECTED ({Status}, reason={Reason}) — clearing the stored session",
                ex.StatusCode, reason);
            _session.Clear();
            return RefreshOutcome.AuthRejected;
        }
        catch (HttpRequestException ex)
        {
            _logger.LogWarning(ex, "Session refresh failed on a network error (reason={Reason})", reason);
            return RefreshOutcome.NetworkError;
        }
        finally
        {
            _refreshLock.Release();
        }
    }

    // ------------------------------------------------------------------
    // §2.2 · the portal redirect is the re-auth signal
    // ------------------------------------------------------------------

    /// <summary>Called by the window when a (cancelled) navigation targeted
    /// the Portal origin. Single-flight; a burst of redirects during one
    /// re-auth is coalesced. A tight cancel→reauth→reload→cancel loop trips
    /// the guard and lands on the signed-out view instead of spinning.</summary>
    public async Task NotifyPortalRedirectAsync(string safeUri)
    {
        if (Interlocked.CompareExchange(ref _reauthInFlight, 1, 0) != 0)
        {
            _logger.LogInformation("Portal redirect while a re-auth is already in flight; coalesced ({Uri})", safeUri);
            return;
        }
        try
        {
            if (TripsRedirectLoopGuard())
            {
                _logger.LogError("RE-AUTH LOOP: 3 portal redirects within 120 s despite successful refreshes — giving up to the signed-out view");
                await HandleSessionLostAsync("reauth_loop");
                return;
            }

            _logger.LogWarning("PORTAL REDIRECT INTERCEPTED: {Uri} — raising host-side re-auth", safeUri);
            var outcome = await RefreshOnceAsync("portal-redirect", _lifetime.Token);
            switch (outcome)
            {
                case RefreshOutcome.Success:
                    await (_surface?.ReloadPageAsync() ?? Task.CompletedTask);
                    _logger.LogInformation("Re-auth via refresh succeeded; page reloaded");
                    return;

                case RefreshOutcome.NetworkError:
                    // Nothing to reload onto; the page stays put and its next
                    // attempt re-signals us once the network returns.
                    _logger.LogWarning("Re-auth deferred: network error during refresh");
                    return;

                case RefreshOutcome.AuthRejected:
                    _logger.LogInformation("Refresh token rejected — falling back to the interactive sran:// flow");
                    SetState(ShellSessionState.SigningIn, "portal-redirect");
                    var ok = await InteractiveSignInAsync(_lifetime.Token);
                    if (ok)
                    {
                        await ApplySessionToBrowserAsync();
                        StartRotation();
                        await (_surface?.NavigatePageAsync() ?? Task.CompletedTask);
                        SetState(ShellSessionState.SignedIn, "reauth");
                        _logger.LogInformation("Re-auth via interactive sign-in succeeded; navigated back to the page");
                    }
                    else
                    {
                        await HandleSessionLostAsync("signin_failed");
                    }
                    return;
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Re-auth after a portal redirect failed unexpectedly");
        }
        finally
        {
            Interlocked.Exchange(ref _reauthInFlight, 0);
        }
    }

    private bool TripsRedirectLoopGuard()
    {
        lock (_recentRedirects)
        {
            var now = DateTime.UtcNow;
            _recentRedirects.Enqueue(now);
            while (_recentRedirects.Count > 0 && (now - _recentRedirects.Peek()) > TimeSpan.FromSeconds(120))
                _recentRedirects.Dequeue();
            return _recentRedirects.Count > 3;
        }
    }

    // ------------------------------------------------------------------
    // §2.5 · logout, both directions
    // ------------------------------------------------------------------

    /// <summary>Host-initiated logout (tray). Clears DPAPI, clears the jar,
    /// blanks the page, lands on the signed-out view. Matches the WPF app's
    /// logout semantics: local clear; no server-side revocation call exists
    /// on IPortalAuthClient today.</summary>
    public async Task SignOutAsync(string reason = "user")
    {
        StopRotation();
        await _auth.LogoutAsync();
        await ClearBrowserSessionAsync();
        SetState(ShellSessionState.SignedOut, reason);
        _logger.LogInformation("Signed out (reason={Reason}): DPAPI cleared, jar cleared, page blanked", reason);
    }

    /// <summary>Server-side loss (revoked/rejected): DPAPI already cleared by
    /// RefreshOnceAsync; clear the browser side and show the signed-out view.</summary>
    private async Task HandleSessionLostAsync(string reason)
    {
        StopRotation();
        await ClearBrowserSessionAsync();
        SetState(ShellSessionState.SignedOut, reason);
    }

    private async Task ClearBrowserSessionAsync()
    {
        if (_surface is null)
            return;
        await _surface.ClearSessionCookiesAsync();
        await _surface.NavigateBlankAsync();
    }

    public void NotifySignedIn(string how) => SetState(ShellSessionState.SignedIn, how);

    private void SetState(ShellSessionState state, string? detail) =>
        StateChanged?.Invoke(state, detail);

    public void Dispose()
    {
        _lifetime.Cancel();
        _lifetime.Dispose();
        _refreshLock.Dispose();
    }
}
