using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Workers;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// Local MCP stdio, spoken to a server running in the capability-free
/// AppContainer from Phase 4.1A.
///
/// Framing here is the MCP wire format — newline-delimited JSON-RPC 2.0 — not
/// the internal worker envelope, because a real MCP server speaks the spec and
/// nothing else. The isolation, Job Object, quotas and cleanup are the interactive
/// worker's; this class only adds the protocol.
///
/// Every request carries a <see cref="DispatchStage"/> so a disconnect can be
/// classified honestly: a request still queued provably did nothing, while one
/// already written to the server's stdin may already have created the record.
/// </summary>
public sealed class IsolatedStdioMcpTransport : IMcpTransport
{
    private const int MaxLineBytes = 4 * 1024 * 1024;
    private const int MaxNotifications = 500;

    private static long _generationCounter;

    private readonly WindowsAppContainerProcessRunner _runner;
    private readonly IsolatedWorkerLaunch _launch;
    private readonly string _workspace;
    private readonly ILogger? _logger;
    private readonly ConcurrentDictionary<long, Pending> _pending = new();
    private readonly ConcurrentDictionary<long, byte> _answered = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly CancellationTokenSource _lifetime = new();
    private readonly List<McpNotification> _notifications = [];
    private readonly object _sync = new();

    private WindowsAppContainerProcessRunner.InteractiveIsolatedProcess? _process;
    private Task? _readLoop;
    private Task? _stderrLoop;
    private long _nextId;
    private long _droppedNotifications;
    private int _disposed;

    public IsolatedStdioMcpTransport(
        WindowsAppContainerProcessRunner runner,
        IsolatedWorkerLaunch launch,
        string workspace,
        ILogger? logger = null)
    {
        _runner = runner;
        _launch = launch;
        _workspace = workspace;
        _logger = logger;
        Generation = Interlocked.Increment(ref _generationCounter);
    }

    public event EventHandler? Exited;

    /// <summary>Increments per transport instance. A reconnect produces a new
    /// generation so a handle from the previous server can never be honoured.</summary>
    public long Generation { get; }

    public int? ProcessId => _process?.ProcessId;

    public bool IsConnected => _process is { HasExited: false };

    public long DroppedNotifications => Interlocked.Read(ref _droppedNotifications);

    public IReadOnlyList<McpNotification> Notifications
    {
        get { lock (_sync) return _notifications.ToList(); }
    }

    public async Task EnsureStartedAsync(CancellationToken ct = default)
    {
        if (_process is not null)
            return;
        ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);

        // No host fallback: if the sandbox cannot host the server, the connector
        // is unavailable, full stop.
        _process = await _runner.StartInteractiveWorkerAsync(_launch, _workspace, ct);
        _readLoop = Task.Run(ReadLoopAsync);
        _stderrLoop = Task.Run(DrainStandardErrorAsync);
    }

    public async Task<JsonRpcResponse> SendRequestAsync(
        string method,
        object? @params,
        CancellationToken ct = default)
    {
        await EnsureStartedAsync(ct);
        var process = _process
                      ?? throw new McpProtocolException("The MCP server is not running.");

        var id = Interlocked.Increment(ref _nextId);
        var pending = new Pending(id);
        _pending[id] = pending;
        try
        {
            var payload = JsonSerializer.SerializeToUtf8Bytes(new
            {
                jsonrpc = "2.0",
                id,
                method,
                @params
            });
            if (payload.Length > MaxLineBytes)
                throw new McpProtocolException(
                    $"An outbound MCP message of {payload.Length} bytes exceeds the limit.");

            using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct, _lifetime.Token);
            try
            {
                await _writeLock.WaitAsync(linked.Token);
            }
            catch (OperationCanceledException)
            {
                pending.Stage = DispatchStage.Queued;
                throw new McpDispatchException(
                    DispatchStage.Queued,
                    "The request was cancelled before it reached the server.");
            }

            try
            {
                pending.Stage = DispatchStage.Writing;
                // Not cancellable: abandoning a half-written line would corrupt
                // the stream for every other in-flight request.
                await process.StandardInput.WriteAsync(payload, CancellationToken.None);
                await process.StandardInput.WriteAsync(
                    "\n"u8.ToArray(), CancellationToken.None);
                await process.StandardInput.FlushAsync(CancellationToken.None);
                pending.Stage = DispatchStage.Dispatched;
            }
            catch (Exception ex) when (ex is IOException or ObjectDisposedException)
            {
                throw new McpDispatchException(
                    pending.Stage,
                    "The MCP server disconnected while the request was being written; "
                    + "whether it acted is unknown.",
                    ex);
            }
            finally
            {
                _writeLock.Release();
            }

            await using var registration = linked.Token.Register(() =>
            {
                pending.Completion.TrySetException(new McpDispatchException(
                    pending.Stage,
                    ct.IsCancellationRequested
                        ? "The request was cancelled after it reached the server."
                        : "The MCP server did not answer in time."));
                _ = SendCancellationAsync(id);
            });

            return await pending.Completion.Task;
        }
        finally
        {
            _pending.TryRemove(id, out _);
        }
    }

    public async Task SendNotificationAsync(
        string method,
        object? @params,
        CancellationToken ct = default)
    {
        await EnsureStartedAsync(ct);
        var process = _process;
        if (process is null)
            return;
        var payload = JsonSerializer.SerializeToUtf8Bytes(new
        {
            jsonrpc = "2.0",
            method,
            @params
        });
        await _writeLock.WaitAsync(ct);
        try
        {
            await process.StandardInput.WriteAsync(payload, ct);
            await process.StandardInput.WriteAsync("\n"u8.ToArray(), ct);
            await process.StandardInput.FlushAsync(ct);
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException)
        {
            // A notification has no reply to lose.
        }
        finally
        {
            _writeLock.Release();
        }
    }

    /// <summary>MCP cancellation is advisory. A server that ignores it still
    /// leaves the request classified as unknown — never as "cancelled, no
    /// effect".</summary>
    private async Task SendCancellationAsync(long requestId)
    {
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            await SendNotificationAsync(
                "notifications/cancelled",
                new { requestId, reason = "the client cancelled this request" },
                timeout.Token);
        }
        catch (Exception ex) when (
            ex is OperationCanceledException or IOException or ObjectDisposedException
                or McpProtocolException)
        {
        }
    }

    private async Task ReadLoopAsync()
    {
        var process = _process!;
        var buffer = new byte[16 * 1024];
        var line = new MemoryStream();
        try
        {
            while (!_lifetime.IsCancellationRequested)
            {
                var read = await process.StandardOutput.ReadAsync(buffer, _lifetime.Token);
                if (read == 0)
                    break;
                for (var index = 0; index < read; index++)
                {
                    if (buffer[index] == (byte)'\n')
                    {
                        Dispatch(line.ToArray());
                        line.SetLength(0);
                        continue;
                    }
                    if (line.Length >= MaxLineBytes)
                        throw new McpProtocolException(
                            "An MCP message exceeded the size limit before a newline arrived.");
                    line.WriteByte(buffer[index]);
                }
            }
            FailAll("The MCP server closed its output before answering.");
        }
        catch (OperationCanceledException)
        {
            FailAll("The MCP session was stopped.");
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException)
        {
            FailAll("The MCP server's output stream failed.");
        }
        catch (McpProtocolException ex)
        {
            _logger?.LogWarning(
                "MCP server violated the wire protocol: {Message}", ex.Message);
            FailAll("The MCP server violated the wire protocol.");
            process.Terminate();
        }
        finally
        {
            Exited?.Invoke(this, EventArgs.Empty);
        }
    }

    private void Dispatch(byte[] rawLine)
    {
        if (rawLine.Length == 0)
            return;
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(rawLine);
        }
        catch (JsonException)
        {
            // The raw body is deliberately not logged: it may carry resource
            // content or credentials the server was handling.
            _logger?.LogWarning(
                "Discarded an unparseable MCP message of {Bytes} bytes", rawLine.Length);
            return;
        }

        using (document)
        {
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
                return;

            // A notification has no id. It must never satisfy a pending request.
            if (!root.TryGetProperty("id", out var idElement)
                || idElement.ValueKind == JsonValueKind.Null)
            {
                RecordNotification(root);
                return;
            }
            if (!idElement.TryGetInt64(out var id))
                return;

            if (!_answered.TryAdd(id, 0))
            {
                _logger?.LogWarning(
                    "Rejected a duplicate MCP response for id {Id}", id);
                return;
            }
            if (!_pending.TryGetValue(id, out var pending))
            {
                // Arrived after the caller gave up, or the id was never ours.
                _logger?.LogDebug("Discarded an unmatched MCP response for id {Id}", id);
                return;
            }

            pending.Stage = DispatchStage.Completed;
            pending.Completion.TrySetResult(new JsonRpcResponse
            {
                Result = root.TryGetProperty("result", out var result)
                    ? result.Clone()
                    : null,
                Error = root.TryGetProperty("error", out var error)
                    ? error.Clone()
                    : null
            });
        }
    }

    private void RecordNotification(JsonElement root)
    {
        var method = root.TryGetProperty("method", out var methodElement)
            ? methodElement.GetString() ?? ""
            : "";
        if (method.Length == 0)
            return;
        lock (_sync)
        {
            if (_notifications.Count >= MaxNotifications)
            {
                // Bounded on purpose: a chatty server must not grow the host's
                // memory without limit.
                Interlocked.Increment(ref _droppedNotifications);
                return;
            }
            _notifications.Add(new McpNotification(
                method,
                root.TryGetProperty("params", out var parameters)
                    ? parameters.GetRawText()
                    : null,
                DateTimeOffset.UtcNow));
        }
    }

    private async Task DrainStandardErrorAsync()
    {
        var process = _process!;
        var buffer = new byte[8192];
        long total = 0;
        try
        {
            while (!_lifetime.IsCancellationRequested)
            {
                var read = await process.StandardError.ReadAsync(buffer, _lifetime.Token);
                if (read == 0)
                    return;
                // Server logs are drained (an unread pipe eventually blocks the
                // server) and counted, but never persisted raw.
                if (Interlocked.Add(ref total, read) > 256 * 1024)
                {
                    _logger?.LogWarning("MCP server exceeded its stderr quota; terminating");
                    process.Terminate();
                    return;
                }
            }
        }
        catch (Exception ex) when (
            ex is OperationCanceledException or IOException or ObjectDisposedException)
        {
        }
    }

    private void FailAll(string reason)
    {
        foreach (var id in _pending.Keys.ToList())
        {
            if (!_pending.TryGetValue(id, out var pending))
                continue;
            pending.Completion.TrySetException(new McpDispatchException(
                pending.Stage, reason));
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
            return;
        _lifetime.Cancel();
        foreach (var loop in new[] { _readLoop, _stderrLoop })
        {
            if (loop is not null)
                await Task.WhenAny(loop, Task.Delay(TimeSpan.FromSeconds(2)));
        }
        if (_process is not null)
            await _process.DisposeAsync();
        _lifetime.Dispose();
        _writeLock.Dispose();
    }

    private sealed class Pending(long id)
    {
        public long Id { get; } = id;
        public DispatchStage Stage { get; set; } = DispatchStage.Queued;
        public TaskCompletionSource<JsonRpcResponse> Completion { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
    }
}

public sealed record McpNotification(string Method, string? ParamsJson, DateTimeOffset ReceivedUtc);

/// <summary>
/// Raised when an MCP request fails in a way whose EFFECT depends on how far it
/// got. <see cref="Stage"/> is what the runtime uses to decide between "retry is
/// safe" and "reconcile before touching this again".
/// </summary>
public sealed class McpDispatchException(
    DispatchStage stage,
    string message,
    Exception? inner = null)
    : Exception(message, inner)
{
    public DispatchStage Stage { get; } = stage;

    /// <summary>True once bytes have left for the server: the operation may
    /// already have taken effect.</summary>
    public bool OutcomeUnknown => Stage >= DispatchStage.Writing;
}
