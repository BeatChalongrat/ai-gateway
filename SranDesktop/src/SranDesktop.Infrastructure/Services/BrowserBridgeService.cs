using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
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
    private const int ProtocolVersion = 2;
    private static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(30);
    private static readonly HashSet<string> AllowedMethods = new(StringComparer.Ordinal)
    {
        "tabs.list", "tabs.activate", "tabs.new", "tabs.close",
        "page.navigate", "page.back", "page.forward", "page.reload",
        "page.read", "page.find", "page.resolve", "page.observe",
        "page.click", "page.type", "page.select", "page.check",
        "page.scroll", "page.upload", "page.download", "page.wait",
        "page.screenshot"
    };
    private static readonly HashSet<string> MutatingMethods = new(StringComparer.Ordinal)
    {
        "tabs.activate", "tabs.new", "tabs.close",
        "page.navigate", "page.back", "page.forward", "page.reload",
        "page.click", "page.type", "page.select", "page.check",
        "page.scroll", "page.upload", "page.download"
    };

    private readonly ICredentialStore _credentials;
    private readonly ILogger<BrowserBridgeService> _logger;
    private readonly string _pipeName;
    private readonly TimeSpan _timeout;
    private readonly CancellationTokenSource _lifetime = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly ConcurrentDictionary<long, PendingRequest> _pending = new();

    private Task? _acceptLoop;
    private volatile NamedPipeServerStream? _client; // non-null AND paired ⇒ usable
    private volatile bool _paired;
    private long _nextId;
    private long _sessionGeneration;
    private string? _token;
    private volatile BrowserBridgeSession? _session;

    public bool IsConnected => _paired && _client is { IsConnected: true };
    public DateTimeOffset? LastSeenAt { get; private set; }
    public BrowserBridgeSession? CurrentSession => _session;
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

    public async Task<JsonElement> RequestAsync(
        string method, object? @params = null,
        CancellationToken ct = default, TimeSpan? timeout = null)
    {
        if (!AllowedMethods.Contains(method))
            throw new BrowserBridgeException($"Unsupported browser bridge method '{method}'.");

        var client = _client;
        var session = _session;
        if (!_paired || client is not { IsConnected: true } || session is null)
            throw new BrowserNotConnectedException();

        var runId = AgentRunExecutionContext.CurrentRunId;
        var stepId = AgentRunExecutionContext.CurrentStepId;
        if (MutatingMethods.Contains(method)
            && (string.IsNullOrWhiteSpace(runId) || string.IsNullOrWhiteSpace(stepId)))
            throw new BrowserBridgeException(
                "RuntimeContextRequired: mutable browser actions must execute inside an AgentRun/AgentStep.");

        var id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        var pending = new PendingRequest(tcs, session.BridgeSessionId, runId, stepId);
        _pending[id] = pending;
        try
        {
            // The deadline covers the WRITE too: a wedged relay that stops
            // draining the pipe must not hang the turn (found the hard way —
            // pipe writes block when the reader stalls).
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeoutCts.CancelAfter(timeout ?? _timeout);

            var payload = JsonSerializer.SerializeToUtf8Bytes(new
            {
                id,
                method,
                @params,
                bridge_session_id = session.BridgeSessionId,
                context = new
                {
                    run_id = runId,
                    step_id = stepId,
                    deadline_unix_ms = DateTimeOffset.UtcNow.Add(
                        timeout ?? _timeout).ToUnixTimeMilliseconds()
                }
            });
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
            {
                tcs.TrySetException(ct.IsCancellationRequested
                    ? new OperationCanceledException(ct)
                    : new TimeoutException($"The Chrome extension did not answer '{method}' in time."));
                _ = SendCancellationAsync(client, session.BridgeSessionId, id);
            });
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

    private async Task AcceptLoopAsync()
    {
        while (!_lifetime.IsCancellationRequested)
        {
            NamedPipeServerStream? server = null;
            try
            {
                // Non-zero buffer quotas: the parameterless-buffer overload
                // creates 0-byte buffers, making every write rendezvous with a
                // read — one stalled reader would freeze writers.
                server = new NamedPipeServerStream(
                    _pipeName, PipeDirection.InOut, maxNumberOfServerInstances: 1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly,
                    inBufferSize: 256 * 1024, outBufferSize: 256 * 1024);
                await server.WaitForConnectionAsync(_lifetime.Token);
                _client = server;
                _logger.LogInformation("Browser relay connected to pipe {Pipe}", _pipeName);
                var nativeConnectionId = $"native_{Guid.NewGuid():N}";
                await ReadLoopAsync(server, nativeConnectionId);
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
                _session = null;
                FailAllPending();
                server?.Dispose();
                if (wasConnected)
                    StatusChanged?.Invoke(this, EventArgs.Empty);
            }
        }
    }

    private async Task ReadLoopAsync(
        NamedPipeServerStream server,
        string nativeConnectionId)
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
                return; // drop the connection — never talk to an unpaired client
            }

            var now = DateTimeOffset.UtcNow;
            LastSeenAt = now;

            if (root.TryGetProperty("hello", out var hello)
                && hello.ValueKind == JsonValueKind.True)
            {
                if (_paired || !TryCreateSession(
                        root,
                        nativeConnectionId,
                        now,
                        out var session))
                    return;

                await BrowserWire.WriteFrameAsync(
                    server,
                    JsonSerializer.SerializeToUtf8Bytes(new
                    {
                        control = "hello_ack",
                        protocol_version = ProtocolVersion,
                        bridge_session_id = session.BridgeSessionId,
                        extension_session_id = session.ExtensionSessionId,
                        native_connection_id = nativeConnectionId,
                        session_generation = session.SessionGeneration
                    }),
                    _lifetime.Token);
                _session = session;
                _paired = true;
                StatusChanged?.Invoke(this, EventArgs.Empty);
                continue;
            }

            if (!_paired || _session is not { } currentSession)
                return;

            if (root.TryGetProperty("heartbeat", out var heartbeat)
                && heartbeat.ValueKind == JsonValueKind.True)
            {
                if (!HasSession(root, currentSession.BridgeSessionId))
                    return;
                _session = currentSession with { LastHeartbeatUtc = now };
                continue;
            }

            if (root.TryGetProperty("id", out var idEl) && idEl.TryGetInt64(out var id)
                && _pending.TryRemove(id, out var pending))
            {
                if (!HasSession(root, pending.BridgeSessionId)
                    || !HasCorrelation(root, "run_id", pending.RunId)
                    || !HasCorrelation(root, "step_id", pending.StepId))
                {
                    pending.Completion.TrySetException(new BrowserBridgeException(
                        "StaleBrowserSession: response identity did not match the dispatched action."));
                    return;
                }

                if (root.TryGetProperty("error", out var errorEl))
                    pending.Completion.TrySetException(new BrowserBridgeException(
                        errorEl.GetString() ?? "The extension reported an error."));
                else if (root.TryGetProperty("result", out var resultEl))
                    pending.Completion.TrySetResult(resultEl.Clone());
                else
                    pending.Completion.TrySetException(
                        new BrowserBridgeException("Malformed extension response."));
            }
        }
    }

    private bool TryCreateSession(
        JsonElement root,
        string nativeConnectionId,
        DateTimeOffset now,
        out BrowserBridgeSession session)
    {
        session = null!;
        if (!root.TryGetProperty("protocol_version", out var protocol)
            || !protocol.TryGetInt32(out var version)
            || version != ProtocolVersion
            || !TryIdentifier(root, "extension_session_id", out var extensionSession)
            || !TryIdentifier(root, "browser_instance_id", out var browserInstance)
            || !TryIdentifier(root, "browser_profile_id", out var browserProfile))
        {
            _logger.LogWarning("Browser pipe client rejected: invalid protocol handshake");
            return false;
        }

        session = new BrowserBridgeSession(
            $"bridge_{Guid.NewGuid():N}",
            extensionSession,
            browserInstance,
            browserProfile,
            nativeConnectionId,
            version,
            Interlocked.Increment(ref _sessionGeneration),
            now,
            now);
        return true;
    }

    private static bool TryIdentifier(
        JsonElement root,
        string name,
        out string value)
    {
        value = root.TryGetProperty(name, out var element)
                && element.ValueKind == JsonValueKind.String
            ? element.GetString() ?? ""
            : "";
        return value.Length is >= 8 and <= 128
               && value.All(character =>
                   char.IsAsciiLetterOrDigit(character)
                   || character is '_' or '-');
    }

    private static bool HasSession(JsonElement root, string expected) =>
        root.TryGetProperty("bridge_session_id", out var session)
        && session.ValueKind == JsonValueKind.String
        && string.Equals(session.GetString(), expected, StringComparison.Ordinal);

    private static bool HasCorrelation(
        JsonElement root,
        string name,
        string? expected)
    {
        if (expected is null)
            return !root.TryGetProperty(name, out var value)
                   || value.ValueKind == JsonValueKind.Null;
        return root.TryGetProperty(name, out var actual)
               && actual.ValueKind == JsonValueKind.String
               && string.Equals(actual.GetString(), expected, StringComparison.Ordinal);
    }

    private async Task SendCancellationAsync(
        NamedPipeServerStream client,
        string bridgeSessionId,
        long requestId)
    {
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(1));
            await _writeLock.WaitAsync(timeout.Token);
            try
            {
                await BrowserWire.WriteFrameAsync(
                    client,
                    JsonSerializer.SerializeToUtf8Bytes(new
                    {
                        control = "cancel",
                        bridge_session_id = bridgeSessionId,
                        request_id = requestId
                    }),
                    timeout.Token);
            }
            finally
            {
                _writeLock.Release();
            }
        }
        catch (Exception ex) when (
            ex is IOException or ObjectDisposedException
                or OperationCanceledException or InvalidDataException)
        {
            // Cancellation is best effort. The durable runtime treats a
            // disconnect after dispatch as unknown outcome, never success.
        }
    }

    private void FailAllPending()
    {
        foreach (var id in _pending.Keys.ToList())
        {
            if (_pending.TryRemove(id, out var pending))
                pending.Completion.TrySetException(new BrowserNotConnectedException());
        }
    }

    private sealed record PendingRequest(
        TaskCompletionSource<JsonElement> Completion,
        string BridgeSessionId,
        string? RunId,
        string? StepId);

    public async ValueTask DisposeAsync()
    {
        _lifetime.Cancel();
        _client?.Dispose();
        if (_acceptLoop is { } loop)
            await Task.WhenAny(loop, Task.Delay(TimeSpan.FromSeconds(2)));
        _lifetime.Dispose();
        _writeLock.Dispose();
    }
}
