using System.Text.Json;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Bridge to the SRAN Chrome extension (browser tools): named-pipe server the
/// BrowserHost relay connects to, with request/response correlation over
/// Chrome native-messaging frames. One paired client at a time.
/// </summary>
public interface IBrowserBridgeService
{
    /// <summary>True while a PAIRED extension connection is alive (a client
    /// that never presented the right token never counts as connected).</summary>
    bool IsConnected { get; }

    /// <summary>Last time any valid message arrived (Settings "last seen").</summary>
    DateTimeOffset? LastSeenAt { get; }

    /// <summary>Raised on connect/disconnect — the Settings status dot.</summary>
    event EventHandler? StatusChanged;

    /// <summary>Starts the pipe server accept loop (idempotent).</summary>
    void Start();

    /// <summary>The pairing token the user pastes into the extension's options
    /// page; generated once and DPAPI-stored.</summary>
    string GetOrCreatePairingToken();

    /// <summary>FIX70: replace the pairing token with a fresh one and drop the
    /// current extension connection. The old token is refused from this call
    /// on (every inbound frame is checked against the current token). The value
    /// is returned only so the host can re-provision pairing.json; it is never
    /// logged.</summary>
    string RotatePairingToken();

    /// <summary>Sends {id, method, params} and awaits the matching {id, result}.
    /// Throws <see cref="Models.BrowserNotConnectedException"/> when no paired
    /// client, TimeoutException after the (default 30 s) deadline, and
    /// <see cref="Models.BrowserBridgeException"/> when the extension answers
    /// with an error message.</summary>
    Task<JsonElement> RequestAsync(
        string method, object? @params = null,
        CancellationToken ct = default, TimeSpan? timeout = null);
}
