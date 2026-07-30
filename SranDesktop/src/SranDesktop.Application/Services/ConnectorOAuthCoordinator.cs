using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;

namespace SranDesktop.Application.Services;

/// <summary>Card-level connect-flow state for an oauth2 connector.</summary>
public enum ConnectorFlowState
{
    NotConnected,
    /// <summary>Ticket minted, browser opened — waiting for the deep link.</summary>
    Connecting,
    Connected,
    Error
}

/// <summary>
/// Drives the broker OAuth connect flow (Phase 7 C3): mints a connect
/// ticket, opens the system browser at the broker's start URL, and later
/// consumes the ``sran://oauth/callback`` deep link. On success the
/// connector is installed locally WITHOUT any secret — oauth2 tool calls
/// authenticate with the live portal JWT, and the provider token stays
/// server-side.
///
/// Events may fire on any thread; UI subscribers marshal to the
/// dispatcher themselves (same contract as IMcpConnectorService.StatusChanged).
/// </summary>
public sealed class ConnectorOAuthCoordinator
{
    private readonly IConnectorAuthClient _authClient;
    private readonly IMcpConnectorService _connectors;
    private readonly IBrowserLauncher _browser;
    private readonly ILogger<ConnectorOAuthCoordinator> _logger;
    // Phase 8 Task 1: deep-link outcomes surface as toasts — this covers the
    // window-was-backgrounded/closed case where the marketplace card is not
    // visible. Optional so Phase 7 tests construct without it.
    private readonly IToastService? _toasts;

    private readonly object _lock = new();
    private readonly Dictionary<string, ConnectorFlowState> _states = new();
    private readonly Dictionary<string, string?> _errorReasons = new();

    public event EventHandler? StateChanged;

    public ConnectorOAuthCoordinator(
        IConnectorAuthClient authClient,
        IMcpConnectorService connectors,
        IBrowserLauncher browser,
        ILogger<ConnectorOAuthCoordinator> logger,
        IToastService? toasts = null)
    {
        _authClient = authClient;
        _connectors = connectors;
        _browser = browser;
        _logger = logger;
        _toasts = toasts;
    }

    public ConnectorFlowState GetState(string connectorId)
    {
        lock (_lock)
        {
            if (_states.TryGetValue(connectorId, out var state))
                return state;
        }
        return _connectors.IsInstalled(connectorId)
            ? ConnectorFlowState.Connected
            : ConnectorFlowState.NotConnected;
    }

    public string? GetErrorReason(string connectorId)
    {
        lock (_lock)
        {
            return _errorReasons.GetValueOrDefault(connectorId);
        }
    }

    /// <summary>Mint a ticket and open the browser. True = browser opened
    /// and we are now Connecting; false = the flow could not start.</summary>
    public async Task<bool> StartConnectAsync(string connectorId, CancellationToken ct = default)
    {
        var ticket = await _authClient.CreateTicketAsync(connectorId, ct);
        if (ticket is null || string.IsNullOrEmpty(ticket.StartUrl))
        {
            SetState(connectorId, ConnectorFlowState.Error, "ticket_failed");
            return false;
        }
        if (!_browser.Open(ticket.StartUrl))
        {
            SetState(connectorId, ConnectorFlowState.Error, "browser_failed");
            return false;
        }
        SetState(connectorId, ConnectorFlowState.Connecting, null);
        return true;
    }

    /// <summary>Entry point for a raw ``sran://`` URI (cold-start arg or the
    /// second-instance pipe). Invalid/hostile URIs are dropped with a log —
    /// never throws, never blocks the caller.</summary>
    public void HandleDeepLink(string uri)
    {
        if (!OAuthDeepLink.TryParse(uri, out var callback) || callback is null)
        {
            _logger.LogWarning("Ignoring malformed sran:// deep link");
            return;
        }
        _ = Task.Run(() => HandleCallbackAsync(callback));
    }

    internal async Task HandleCallbackAsync(OAuthCallbackResult callback)
    {
        try
        {
            if (!callback.Success)
            {
                _logger.LogWarning(
                    "Connector {Id} OAuth failed: {Reason}",
                    callback.ConnectorId, callback.Reason ?? "unknown");
                SetState(
                    callback.ConnectorId, ConnectorFlowState.Error,
                    callback.Reason ?? "unknown");
                _toasts?.Show(ToastKind.Error, "toast.connectorFailed", callback.ConnectorId);
                return;
            }

            // The deep link only ASSERTS success — confirm with the backend
            // before trusting it (any local process can fire sran:// URIs).
            var connections = await _authClient.GetConnectionsAsync();
            var confirmed = connections.Any(c =>
                c.ConnectorId == callback.ConnectorId && c.Connected);
            if (!confirmed)
            {
                SetState(callback.ConnectorId, ConnectorFlowState.Error, "not_confirmed");
                _toasts?.Show(ToastKind.Error, "toast.connectorFailed", callback.ConnectorId);
                return;
            }

            // Install WITHOUT a local secret — the key Phase 7 change. The
            // approval recorded here is the OAuth consent the user just gave in
            // their browser, which the backend has confirmed above: the deep link
            // alone would only be an assertion, and an assertion any local process
            // can make is not a decision.
            await _connectors.InstallAsync(
                callback.ConnectorId, apiToken: null,
                authMode: Models.InstalledConnector.AuthModeOAuth2,
                evidence: Interfaces.UserApprovalEvidence.FromUserGesture(
                    "user:oauth-consent", "broker OAuth consent confirmed by the backend"));
            await _connectors.PingAsync(callback.ConnectorId);
            SetState(callback.ConnectorId, ConnectorFlowState.Connected, null);
            _toasts?.Show(ToastKind.Success, "toast.connectorConnected", callback.ConnectorId);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Connector {Id} callback handling failed",
                callback.ConnectorId);
            SetState(callback.ConnectorId, ConnectorFlowState.Error, "callback_failed");
            _toasts?.Show(ToastKind.Error, "toast.connectorFailed", callback.ConnectorId);
        }
    }

    /// <summary>Uninstall locally + best-effort server-side token delete.</summary>
    public async Task DisconnectAsync(string connectorId, CancellationToken ct = default)
    {
        await _connectors.UninstallAsync(connectorId, ct);
        SetState(connectorId, ConnectorFlowState.NotConnected, null);
        try
        {
            await _authClient.DisconnectAsync(connectorId, ct);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Server-side disconnect for {Id} failed", connectorId);
        }
    }

    private void SetState(string connectorId, ConnectorFlowState state, string? errorReason)
    {
        lock (_lock)
        {
            _states[connectorId] = state;
            _errorReasons[connectorId] = errorReason;
        }
        StateChanged?.Invoke(this, EventArgs.Empty);
    }
}
