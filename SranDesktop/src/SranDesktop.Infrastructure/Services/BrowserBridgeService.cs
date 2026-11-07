using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Native.Browser;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Browser-bridge pipe server (browser tools). One BrowserHost relay client
/// at a time; reconnect-friendly — Chrome restarts spawn a fresh relay and the
/// accept loop simply takes the next connection. Protocol (JSON per frame):
/// app→ext {id, method, params}; ext→app {token, hello} once after connect,
/// then {token, id, result|error}. EVERY inbound message must carry the DPAPI
/// pairing token — a mismatch drops the connection, so a rogue local process
/// that finds the pipe cannot answer (or issue) anything.
/// </summary>
public sealed class BrowserBridgeService : IBrowserBridgeService, IAsyncDisposable
{
    private const string TokenCredentialKey = "browser:pairing-token";
    private static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(30);

    private readonly ICredentialStore _credentials;
    private readonly ILogger<BrowserBridgeService> _logger;
    private readonly string _pipeName;
    private readonly TimeSpan _timeout;
    private readonly CancellationTokenSource _lifetime = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> _pending = new();

    private Task? _acceptLoop;
    private volatile NamedPipeServerStream? _client; // non-null AND paired ⇒ usable
    private volatile bool _paired;
    private long _nextId;
    private string? _token;

    public bool IsConnected => _paired && _client is { IsConnected: true };
    public DateTimeOffset? LastSeenAt { get; private set; }
    public event EventHandler? StatusChanged;

    public BrowserBridgeService(
        ICredentialStore credentials, ILogger<BrowserBridgeService> logger,
        string? pipeName = null, TimeSpan? defaultTimeout = null)
    {
        _credentials = credentials;
        _logger = logger;
        _pipeName = pipeName ?? BrowserWire.PipeName();
        _timeout = defaultTimeout ?? DefaultTimeout;
    }

    public void Start()
    {
        _acceptLoop ??= Task.Run(AcceptLoopAsync);
    }

    public string GetOrCreatePairingToken()
    {
        if (_token is not null)
            return _token;
        var stored = _credentials.Load(TokenCredentialKey);
        if (!string.IsNullOrEmpty(stored))
            return _token = stored;

        // URL-safe so it survives copy-paste into the extension options page.
        var generated = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
        _credentials.Save(TokenCredentialKey, generated);
        return _token = generated;
    }

    /// <summary>FIX70: a fresh token replaces the stored one at once; the live
    /// connection (if any) is dropped so the next hello must carry the new
    /// token. Every read loop compares against the CURRENT token, so the old
    /// value is refused from here on. The value is never logged.</summary>
    public string RotatePairingToken()
    {
        var generated = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
        _credentials.Save(TokenCredentialKey, generated);
        _token = generated;
        var client = _client;
        var dropped = client is not null;
        client?.Dispose(); // the read loop's finally clears _client/_paired and raises StatusChanged
        _logger.LogInformation("Browser pairing token rotated (value never logged); live connection dropped: {Dropped}", dropped);
        return generated;
    }

    public async Task<JsonElement> RequestAsync(
        string method, object? @params = null,
        CancellationToken ct = default, TimeSpan? timeout = null)
    {
        var client = _client;
        if (!_paired || client is not { IsConnected: true })
            throw new BrowserNotConnectedException();

        var id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = tcs;
        try
        {
            // The deadline covers the WRITE too: a wedged relay that stops
            // draining the pipe must not hang the turn (found the hard way —
            // pipe writes block when the reader stalls).
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeoutCts.CancelAfter(timeout ?? _timeout);

            var payload = JsonSerializer.SerializeToUtf8Bytes(new { id, method, @params });
            await _writeLock.WaitAsync(timeoutCts.Token);
            try
            {
                await BrowserWire.WriteFrameAsync(client, payload, timeoutCts.Token);
            }
            finally
            {
                _writeLock.Release();
            }

            await using var registration = timeoutCts.Token.Register(() =>
                tcs.TrySetException(ct.IsCancellationRequested
                    ? new OperationCanceledException(ct)
                    : new TimeoutException($"The Chrome extension did not answer '{method}' in time.")));
            return await tcs.Task;
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw new TimeoutException($"The Chrome extension did not answer '{method}' in time.");
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException or InvalidDataException)
        {
            throw new BrowserNotConnectedException();
        }
        finally
        {
            _pending.TryRemove(id, out _);
        }
    }

    /// <summary>Pause between attempts to CREATE the pipe when another process
    /// already owns it (maxNumberOfServerInstances: 1). Shell Phase 1 found the
    /// loop retrying immediately, forever, when the WPF app and the shell ran
    /// side by side — 160 MB of "All pipe instances are busy" in ten seconds.</summary>
    private static readonly TimeSpan PipeBusyRetryDelay = TimeSpan.FromSeconds(10);

    private async Task AcceptLoopAsync()
    {
        var pipeBusyLogged = false;
        while (!_lifetime.IsCancellationRequested)
        {
            NamedPipeServerStream? server = null;
            try
            {
                // Non-zero buffer quotas: the parameterless-buffer overload
                // creates 0-byte buffers, making every write rendezvous with a
                // read — one stalled reader would freeze writers.
                try
                {
                    server = new NamedPipeServerStream(
                        _pipeName, PipeDirection.InOut, maxNumberOfServerInstances: 1,
                        PipeTransmissionMode.Byte, PipeOptions.Asynchronous,
                        inBufferSize: 256 * 1024, outBufferSize: 256 * 1024);
                }
                catch (IOException ex)
                {
                    // Another host owns the pipe. Warn once, then wait — never spin.
                    if (!pipeBusyLogged)
                    {
                        pipeBusyLogged = true;
                        _logger.LogWarning(ex,
                            "Browser bridge pipe {Pipe} is owned by another process (another SRAN Desktop host?) — retrying every {Delay}s; browser tools stay unavailable here until it is released",
                            _pipeName, PipeBusyRetryDelay.TotalSeconds);
                    }
                    await Task.Delay(PipeBusyRetryDelay, _lifetime.Token).ConfigureAwait(false);
                    continue;
                }
                pipeBusyLogged = false;
                await server.WaitForConnectionAsync(_lifetime.Token).ConfigureAwait(false);
                _client = server;
                _logger.LogInformation("Browser relay connected to pipe {Pipe}", _pipeName);
                await ReadLoopAsync(server).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex) when (ex is IOException or ObjectDisposedException or InvalidDataException)
            {
                _logger.LogDebug(ex, "Browser pipe session ended");
            }
            finally
            {
                var wasConnected = IsConnected;
                _client = null;
                _paired = false;
                FailAllPending();
                server?.Dispose();
                if (wasConnected)
                    StatusChanged?.Invoke(this, EventArgs.Empty);
            }
        }
    }

    private async Task ReadLoopAsync(NamedPipeServerStream server)
    {
        var expectedToken = GetOrCreatePairingToken();
        while (await BrowserWire.ReadFrameAsync(server, _lifetime.Token) is { } frame)
        {
            using var doc = JsonDocument.Parse(frame);
            var root = doc.RootElement;

            // Pairing gate: EVERY inbound message must carry the right token.
            var token = root.TryGetProperty("token", out var tokenEl) ? tokenEl.GetString() : null;
            if (!string.Equals(token, expectedToken, StringComparison.Ordinal))
            {
                _logger.LogWarning("Browser pipe client rejected: pairing token mismatch");
                // FIX-B: tell the extension WHY before dropping, so its options
                // page can say "paste a fresh token" instead of a silent grey
                // dot. Reveals nothing: the client already knows its own token.
                await TrySendControlAsync(server, new { hello_reject = "token_mismatch" });
                return; // drop the connection — never talk to an unpaired client
            }

            LastSeenAt = DateTimeOffset.UtcNow;

            if (root.TryGetProperty("hello", out _))
            {
                if (!_paired)
                {
                    _paired = true;
                    StatusChanged?.Invoke(this, EventArgs.Empty);
                }
                // FIX-B: explicit ack — the extension shows "connected" only
                // after this arrives (its old status was optimistic-on-send).
                await TrySendControlAsync(server, new { hello_ack = true });
                continue;
            }

            if (root.TryGetProperty("id", out var idEl) && idEl.TryGetInt64(out var id)
                && _pending.TryRemove(id, out var tcs))
            {
                if (root.TryGetProperty("error", out var errorEl))
                    tcs.TrySetException(new BrowserBridgeException(
                        errorEl.GetString() ?? "The extension reported an error."));
                else if (root.TryGetProperty("result", out var resultEl))
                    tcs.TrySetResult(resultEl.Clone());
                else
                    tcs.TrySetException(new BrowserBridgeException("Malformed extension response."));
            }
        }
    }

    /// <summary>Best-effort control frame (hello ack/reject). Id-less, so an
    /// extension that predates it ignores it (onRequest drops frames without
    /// an id). A broken pipe here just ends the session as before.</summary>
    private async Task TrySendControlAsync(NamedPipeServerStream server, object message)
    {
        try
        {
            var payload = JsonSerializer.SerializeToUtf8Bytes(message);
            await _writeLock.WaitAsync(_lifetime.Token);
            try
            {
                await BrowserWire.WriteFrameAsync(server, payload, _lifetime.Token);
            }
            finally
            {
                _writeLock.Release();
            }
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException
                                       or InvalidDataException or OperationCanceledException)
        {
            _logger.LogDebug(ex, "Browser control frame not delivered");
        }
    }

    private void FailAllPending()
    {
        foreach (var id in _pending.Keys.ToList())
        {
            if (_pending.TryRemove(id, out var tcs))
                tcs.TrySetException(new BrowserNotConnectedException());
        }
    }

    public async ValueTask DisposeAsync()
    {
        _lifetime.Cancel();
        _client?.Dispose();
        if (_acceptLoop is { } loop)
            await Task.WhenAny(loop, Task.Delay(TimeSpan.FromSeconds(2))).ConfigureAwait(false);
        _lifetime.Dispose();
        _writeLock.Dispose();
    }
}
