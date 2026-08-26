using System.Security.Cryptography;
using System.Text.Json;
using System.Windows.Threading;
using Microsoft.Extensions.Logging;
using Microsoft.Web.WebView2.Core;
using SranDesktop.Shell.Hosting;

namespace SranDesktop.Shell.Bridge;

/// <summary>
/// The one channel between page and host (DESKTOP_SHELL_PLAN.md §5), and the
/// ONLY file in the shell allowed to touch web messages — pinned by
/// ShellPinningTests.
///
///   C-1  Registers the TOP-LEVEL CoreWebView2.WebMessageReceived only. Never
///        CoreWebView2Frame.WebMessageReceived, never FrameCreated (the only
///        way to reach a frame handler), never AddHostObjectToScript. Spike
///        finding 6: frame-scoped delivery is how a sandboxed artifact iframe
///        reached the host; with no frame handler it has no path at all.
///   C-2  Every inbound event's e.Source is parsed as a URI and compared BY
///        ORIGIN to the configured page origin. Never StartsWith — see
///        IsExpectedSource, quoted in the Phase 3 report.
///   C-3  A per-navigation nonce, minted here and handed to the page via
///        ExecuteScriptAsync on the TOP document only (ExecuteScriptAsync
///        never runs in child frames; AddScriptToExecuteOnDocumentCreated
///        does, which is exactly why it is not used for this).
///   C-4  The inbound type set is closed (BridgeProtocol). Unknown → drop+log.
///
/// Every inbound event — accepted or dropped — is logged, so "the log shows
/// nothing arrived" is evidence from a channel proven to record arrivals.
/// </summary>
public sealed class BridgeChannel
{
    private readonly NavigationPolicy _policy;
    private readonly ILogger<BridgeChannel> _logger;
    private readonly string _pageOrigin;

    private CoreWebView2? _core;
    private Dispatcher? _dispatcher;
    private string _nonce = MintNonce(); // replaced on every page navigation

    public BridgeChannel(ShellOptions options, NavigationPolicy policy, ILogger<BridgeChannel> logger)
    {
        _policy = policy;
        _logger = logger;
        _pageOrigin = NavigationPolicy.OriginOf(new Uri(options.PageUrl));
    }

    public event Action<TurnSubmitPayload>? TurnSubmitReceived;
    public event Action<TurnCancelPayload>? TurnCancelReceived;
    public event Action<ConsentRespondPayload>? ConsentRespondReceived;
    public event Action<UiReadyPayload>? UiReadyReceived;
    // Phase 5 control vocabulary — the ONE new pair, dispatched down a separate
    // closed branch from the four turn types (SHELL_PHASE5_REPORT.md §2.2).
    public event Action<HostQuery>? HostQueryReceived;

    /// <summary>Top-level handler registration — the single registration in
    /// the whole shell.</summary>
    public void Attach(CoreWebView2 core, Dispatcher dispatcher)
    {
        _core = core;
        _dispatcher = dispatcher;
        core.WebMessageReceived += OnWebMessageReceived;
        _logger.LogInformation("Bridge attached: top-level WebMessageReceived only; expected origin {Origin}", _pageOrigin);
    }

    /// <summary>C-3: new nonce per top-level page navigation, delivered with
    /// ExecuteScriptAsync (top document only). Call after a successful
    /// NavigationCompleted on the page origin.</summary>
    public async Task InjectNonceAsync()
    {
        if (_core is null || _dispatcher is null)
            return;
        _nonce = MintNonce();
        var script =
            "(function(){" +
            $"window.__SRAN_BRIDGE__=Object.freeze({{nonce:\"{_nonce}\"}});" +
            "try{window.dispatchEvent(new CustomEvent('sran:bridge-ready'));}catch(e){}" +
            "})();";
        await _dispatcher.InvokeAsync(() => _core.ExecuteScriptAsync(script)).Task.Unwrap();
        _logger.LogInformation("Bridge nonce injected into the top document (sha256/8={Fp})", Fingerprint(_nonce));
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        // C-2 — origin equality, never prefix matching.
        if (!IsExpectedSource(e.Source))
        {
            _logger.LogWarning("BRIDGE INBOUND DROPPED: source-origin-mismatch (source={Source})", SafeOrigin(e.Source));
            return;
        }

        string json;
        try
        {
            json = e.WebMessageAsJson;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "BRIDGE INBOUND DROPPED: unreadable message");
            return;
        }

        // Shared gate for BOTH vocabularies: JSON-object + nonce (C-3). The type
        // is still a raw string here — the branch on turn-vs-control is below,
        // and each branch is closed independently.
        if (!BridgeProtocol.TryReadEnvelope(json, _nonce, out var typeString, out var payload, out var envReason))
        {
            _logger.LogWarning("BRIDGE INBOUND DROPPED: {Reason} (source={Source}, {Bytes} bytes)",
                envReason, SafeOrigin(e.Source), json.Length);
            return;
        }

        // Control vocabulary (Phase 5): exactly host.query. Kept OUT of the turn
        // enum so InboundMessageType stays four and its pinning test is untouched.
        if (typeString == "host.query")
        {
            var query = HostQueryProtocol.TryParseHostQuery(payload, out var queryReason);
            if (query is null)
            {
                _logger.LogWarning("BRIDGE INBOUND DROPPED: host.query invalid: {Reason}", queryReason);
                return;
            }
            _logger.LogInformation("BRIDGE HOST.QUERY: {Op} (req={Req}) from {Source}",
                query.Op, query.RequestId, SafeOrigin(e.Source));
            HostQueryReceived?.Invoke(query);
            return;
        }

        // Turn vocabulary: the closed four. Unknown → drop + log (C-4).
        var turnType = BridgeProtocol.ParseInboundType(typeString);
        if (turnType is null)
        {
            _logger.LogWarning("BRIDGE INBOUND DROPPED: unknown-type:{Type} (source={Source})",
                typeString, SafeOrigin(e.Source));
            return;
        }

        _logger.LogInformation("BRIDGE INBOUND: {Type} from {Source}", turnType, SafeOrigin(e.Source));
        switch (turnType.Value)
        {
            case InboundMessageType.TurnSubmit:
                var submit = BridgeProtocol.TryParseTurnSubmit(payload, out var submitReason);
                if (submit is null)
                {
                    _logger.LogWarning("BRIDGE INBOUND DROPPED: turn.submit payload invalid: {Reason}", submitReason);
                    PostHostError("invalid_payload", "The message could not be processed.");
                    return;
                }
                TurnSubmitReceived?.Invoke(submit);
                return;

            case InboundMessageType.TurnCancel:
                var cancel = BridgeProtocol.TryParseTurnCancel(payload, out var cancelReason);
                if (cancel is null)
                {
                    _logger.LogWarning("BRIDGE INBOUND DROPPED: turn.cancel payload invalid: {Reason}", cancelReason);
                    return;
                }
                TurnCancelReceived?.Invoke(cancel);
                return;

            case InboundMessageType.ConsentRespond:
                var consent = BridgeProtocol.TryParseConsentRespond(payload, out var consentReason);
                if (consent is null)
                {
                    _logger.LogWarning("BRIDGE INBOUND DROPPED: consent.respond payload invalid: {Reason}", consentReason);
                    return;
                }
                ConsentRespondReceived?.Invoke(consent);
                return;

            case InboundMessageType.UiReady:
                var ready = BridgeProtocol.TryParseUiReady(payload, out var readyReason);
                if (ready is null)
                {
                    _logger.LogWarning("BRIDGE INBOUND DROPPED: ui.ready payload invalid: {Reason}", readyReason);
                    return;
                }
                _logger.LogInformation("BRIDGE HANDSHAKE: ui.ready (nonce verified) conv={Conv} lastSeq={Seq}",
                    ready.ConversationId ?? "(none)", ready.LastSeq);
                UiReadyReceived?.Invoke(ready);
                return;
        }
        // No default: the switch is exhaustive over the closed enum (C-4);
        // unknown wire types never reach this point.
    }

    /// <summary>C-2. Quoted verbatim in SHELL_PHASE3_REPORT.md.</summary>
    private bool IsExpectedSource(string source) =>
        Uri.TryCreate(source, UriKind.Absolute, out var uri)
        && uri.Scheme is "http" or "https"
        && string.Equals(NavigationPolicy.OriginOf(uri), _pageOrigin, StringComparison.OrdinalIgnoreCase);

    // ------------------------------------------------------------------
    // Host → page (typed JSON via PostWebMessageAsJson; never AsString)
    // ------------------------------------------------------------------

    public void PostTurnEvent(object payload) => Post(OutboundMessageType.TurnEvent, payload);

    public void PostConsentRequest(object payload) => Post(OutboundMessageType.ConsentRequest, payload);

    /// <summary>Phase 5 control reply. Not an OutboundMessageType (the turn set
    /// stays four); posted under its own wire name so the turn vocabulary is
    /// untouched. Correlated to the request by the page-minted requestId, which
    /// the host echoes and never interprets.</summary>
    public void PostHostResult(string requestId, bool ok, object? data = null, string? error = null)
    {
        var core = _core;
        var dispatcher = _dispatcher;
        if (core is null || dispatcher is null)
            return;
        var json = JsonSerializer.Serialize(
            new { type = HostQueryProtocol.ResultWireName, payload = new { requestId, ok, data, error } },
            BridgeProtocol.JsonOptions);
        _ = dispatcher.InvokeAsync(() =>
        {
            try
            {
                core.PostWebMessageAsJson(json);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Bridge host.result post failed (req={Req})", requestId);
            }
        });
    }

    public void PostHostError(string code, string message) =>
        Post(OutboundMessageType.HostError, new { code, message });

    // session.changed: defined by the vocabulary, deliberately UNSENT.
    // Phase 2 verified the page needs no telling about cookie rotation
    // (cookies are HttpOnly, sent per request, and the page caches no token).
    // The method exists so the type is real; nothing calls it.
    public void PostSessionChanged(object payload) => Post(OutboundMessageType.SessionChanged, payload);

    private void Post(OutboundMessageType type, object payload)
    {
        var core = _core;
        var dispatcher = _dispatcher;
        if (core is null || dispatcher is null)
            return;
        var json = JsonSerializer.Serialize(
            new { type = BridgeProtocol.WireName(type), payload },
            BridgeProtocol.JsonOptions);
        // Fire-and-forget onto the UI thread; WebView2 objects are STA-bound.
        _ = dispatcher.InvokeAsync(() =>
        {
            try
            {
                core.PostWebMessageAsJson(json);
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Bridge post failed ({Type})", type);
            }
        });
    }

    private static string MintNonce()
    {
        var bytes = RandomNumberGenerator.GetBytes(32);
        return Convert.ToBase64String(bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=');
    }

    private static string Fingerprint(string value)
    {
        var hash = SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(value));
        return Convert.ToHexString(hash)[..8].ToLowerInvariant();
    }

    private static string SafeOrigin(string source) =>
        Uri.TryCreate(source, UriKind.Absolute, out var uri)
            ? NavigationPolicy.OriginOf(uri)
            : source.Length <= 64 ? source : source[..64] + "...";
}
