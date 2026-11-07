using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// MCP stdio transport (Phase 12 D): spawns a local MCP server process and
/// speaks NEWLINE-DELIMITED JSON-RPC 2.0 over its stdin/stdout — one JSON
/// object per line, which is what the MCP spec's stdio transport defines and
/// what real servers (@modelcontextprotocol/server-*) implement. This repo's
/// own services/mcp-gateway wraps such servers with supergateway, whose stdio
/// side writes <c>JSON.stringify(msg) + '\n'</c> and splits reads on
/// <c>/\r?\n/</c> — NOT LSP-style Content-Length framing. We match that:
/// requests are one line ending in '\n'; responses are read line-by-line with
/// trailing '\r' tolerated (Windows servers emit \r\n).
///
/// A background reader loop matches responses to pending requests by JSON-RPC
/// id; server notifications / server-initiated requests are logged and
/// skipped. stderr is drained to the log. The process is spawned on first use
/// (no shell window), and once it exits — crash or otherwise — every in-flight
/// call fails with <see cref="McpProtocolException"/> and <see cref="Exited"/>
/// fires so the owner can surface Disconnected; the transport does not respawn
/// (a re-dial builds a fresh transport and re-runs the MCP handshake).
/// Disposal kills the process (whole tree) after a short graceful stdin-close.
/// </summary>
public sealed class StdioMcpTransport : IMcpTransport
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    private readonly string _command;
    private readonly IReadOnlyList<string> _args;
    private readonly IReadOnlyDictionary<string, string>? _env;
    private readonly ILogger? _logger;
    private readonly TimeSpan _requestTimeout;
    private readonly SemaphoreSlim _lifecycleLock = new(1, 1);
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonRpcResponse>> _pending = new();

    private Process? _process;
    private Task? _stdoutLoop;
    private Task? _stderrLoop;
    private long _nextId;
    private volatile bool _started;
    private volatile bool _disposed;

    public StdioMcpTransport(
        string command,
        IReadOnlyList<string>? args = null,
        IReadOnlyDictionary<string, string>? env = null,
        ILogger? logger = null,
        TimeSpan? requestTimeout = null)
    {
        _command = command;
        _args = args ?? Array.Empty<string>();
        _env = env;
        _logger = logger;
        // A hung-but-alive server must not park the turn forever (HTTP has
        // HttpClient's timeout; stdio had none). Generous default — real
        // tool calls can legitimately run long.
        _requestTimeout = requestTimeout ?? TimeSpan.FromSeconds(120);
    }

    /// <summary>Fires when the server process exits for any reason other than
    /// our own disposal (crash, clean self-exit, external kill).</summary>
    public event EventHandler? Exited;

    /// <summary>Spawned server pid — test seam. Null before first use.</summary>
    internal int? ProcessId
    {
        get
        {
            try { return _process?.Id; }
            catch (InvalidOperationException) { return null; }
        }
    }

    private bool IsAlive
    {
        get
        {
            try { return _process is { HasExited: false }; }
            catch (InvalidOperationException) { return false; }
        }
    }

    public async Task EnsureStartedAsync(CancellationToken ct = default)
    {
        if (IsAlive)
            return;

        await _lifecycleLock.WaitAsync(ct);
        try
        {
            if (_disposed)
                throw new McpProtocolException($"MCP stdio transport for '{_command}' is disposed");
            if (IsAlive)
                return;
            if (_started)
                // The process died. No silent respawn: the MCP session state
                // (initialize handshake) lives above this layer, so the owner
                // must re-dial with a fresh transport.
                throw new McpProtocolException($"MCP stdio server '{_command}' exited");

            var psi = new ProcessStartInfo
            {
                FileName = _command,
                UseShellExecute = false,   // required for redirection; no shell window
                CreateNoWindow = true,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                // UTF-8 WITHOUT BOM: a BOM would prefix the first JSON line
                // and break the server's parser.
                StandardInputEncoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false),
                StandardOutputEncoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false),
                StandardErrorEncoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false)
            };
            foreach (var arg in _args)
                psi.ArgumentList.Add(arg);
            if (_env is not null)
                foreach (var (key, value) in _env)
                    psi.Environment[key] = value;

            Process process;
            try
            {
                process = Process.Start(psi)
                    ?? throw new McpProtocolException($"Failed to start MCP stdio server '{_command}'");
            }
            catch (Exception ex) when (ex is System.ComponentModel.Win32Exception
                                           or InvalidOperationException or IOException)
            {
                // Normalize spawn failures to the exception type every MCP
                // call site already handles (→ Disconnected, never a crash).
                throw new McpProtocolException(
                    $"Failed to start MCP stdio server '{_command}': {ex.Message}");
            }

            _process = process;
            _started = true;
            _logger?.LogInformation("MCP stdio server '{Command}' started (pid {Pid})",
                _command, process.Id);
            _stdoutLoop = Task.Run(() => ReadStdoutAsync(process));
            _stderrLoop = Task.Run(() => ReadStderrAsync(process));
        }
        finally
        {
            _lifecycleLock.Release();
        }
    }

    public async Task<JsonRpcResponse> SendRequestAsync(
        string method, object? @params, CancellationToken ct = default)
    {
        await EnsureStartedAsync(ct);

        var id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonRpcResponse>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = tcs;
        try
        {
            await WriteLineAsync(BuildEnvelope(method, @params, id), ct);
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeoutCts.CancelAfter(_requestTimeout);
            await using var registration =
                timeoutCts.Token.Register(() => tcs.TrySetCanceled(timeoutCts.Token));
            try
            {
                return await tcs.Task;
            }
            catch (OperationCanceledException)
            {
                if (ct.IsCancellationRequested)
                    throw; // caller cancelled — preserve the original behavior
                throw new McpProtocolException(
                    $"MCP request '{method}' timed out after {_requestTimeout.TotalSeconds:0}s");
            }
        }
        finally
        {
            _pending.TryRemove(id, out _);
        }
    }

    public async Task SendNotificationAsync(
        string method, object? @params, CancellationToken ct = default)
    {
        await EnsureStartedAsync(ct);
        await WriteLineAsync(BuildEnvelope(method, @params, null), ct);
    }

    private static string BuildEnvelope(string method, object? @params, long? id)
    {
        var payload = new Dictionary<string, object?>
        {
            ["jsonrpc"] = "2.0",
            ["method"] = method
        };
        if (id is not null)
            payload["id"] = id;
        if (@params is not null)
            payload["params"] = @params;
        // Serialize() never emits raw newlines, so the one-message-per-line
        // invariant of the stdio framing holds.
        return JsonSerializer.Serialize(payload, JsonOptions);
    }

    private async Task WriteLineAsync(string json, CancellationToken ct)
    {
        var process = _process
            ?? throw new McpProtocolException($"MCP stdio server '{_command}' is not running");
        await _writeLock.WaitAsync(ct);
        try
        {
            // '\n' delimiter per the MCP stdio spec (WriteLineAsync would emit
            // \r\n on Windows; most servers tolerate it, plain \n all do).
            await process.StandardInput.WriteAsync((json + "\n").AsMemory(), ct);
            await process.StandardInput.FlushAsync(ct);
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException
                                       or InvalidOperationException)
        {
            throw new McpProtocolException(
                $"MCP stdio server '{_command}' is not accepting input: {ex.Message}");
        }
        finally
        {
            _writeLock.Release();
        }
    }

    private async Task ReadStdoutAsync(Process process)
    {
        try
        {
            // ReadLineAsync strips '\n' AND a trailing '\r' — the required
            // \r\n tolerance comes for free.
            while (await process.StandardOutput.ReadLineAsync() is { } line)
            {
                if (string.IsNullOrWhiteSpace(line))
                    continue;
                HandleLine(line);
            }
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException
                                       or OperationCanceledException)
        {
            _logger?.LogDebug(ex, "MCP stdio '{Command}': stdout closed", _command);
        }
        OnProcessGone();
    }

    private void HandleLine(string line)
    {
        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(line);
        }
        catch (JsonException ex)
        {
            // A chatty server printing to stdout must not kill the reader loop.
            _logger?.LogWarning(ex, "MCP stdio '{Command}': ignoring non-JSON stdout line: {Line}",
                _command, Truncate(line));
            return;
        }

        using (doc)
        {
            var root = doc.RootElement;
            // A RESPONSE has an id and no method. Server notifications and
            // server→client requests carry "method" — not ours to answer here;
            // log and skip so the reader loop keeps draining.
            if (!root.TryGetProperty("method", out _)
                && root.TryGetProperty("id", out var idElement)
                && idElement.ValueKind == JsonValueKind.Number
                && idElement.TryGetInt64(out var id)
                && _pending.TryRemove(id, out var tcs))
            {
                tcs.TrySetResult(new JsonRpcResponse
                {
                    Result = root.TryGetProperty("result", out var result) ? result.Clone() : null,
                    Error = root.TryGetProperty("error", out var error) ? error.Clone() : null
                });
                return;
            }
            _logger?.LogDebug("MCP stdio '{Command}': skipping non-response message: {Line}",
                _command, Truncate(line));
        }
    }

    private async Task ReadStderrAsync(Process process)
    {
        try
        {
            while (await process.StandardError.ReadLineAsync() is { } line)
            {
                if (!string.IsNullOrWhiteSpace(line))
                    _logger?.LogInformation("MCP stdio '{Command}' stderr: {Line}",
                        _command, Truncate(line));
            }
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException
                                       or OperationCanceledException)
        {
            // stderr closed with the process; nothing to do.
        }
    }

    private void OnProcessGone()
    {
        // Fail every in-flight call so no caller hangs on a dead process.
        foreach (var id in _pending.Keys.ToList())
        {
            if (_pending.TryRemove(id, out var tcs))
                tcs.TrySetException(new McpProtocolException(
                    $"MCP stdio server '{_command}' exited"));
        }
        if (!_disposed)
        {
            _logger?.LogWarning("MCP stdio server '{Command}' exited unexpectedly", _command);
            Exited?.Invoke(this, EventArgs.Empty);
        }
    }

    private static string Truncate(string line) =>
        line.Length > 400 ? line[..400] + "…" : line;

    public async ValueTask DisposeAsync()
    {
        if (_disposed)
            return;
        _disposed = true; // set BEFORE the kill so Exited stays silent on disposal

        var process = _process;
        if (process is not null)
        {
            try
            {
                // Graceful MCP shutdown signal first: close stdin, give the
                // server a moment to exit on its own…
                try { process.StandardInput.Close(); }
                catch (Exception ex) when (ex is IOException or ObjectDisposedException
                                               or InvalidOperationException) { }

                using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                try
                {
                    await process.WaitForExitAsync(cts.Token);
                }
                catch (OperationCanceledException)
                {
                    // …then kill the whole tree (connector removal / app exit
                    // must never leave an orphaned local server running).
                    try { process.Kill(entireProcessTree: true); }
                    catch (InvalidOperationException) { } // already gone
                }
            }
            finally
            {
                // Let the reader loops drain to EOF (bounded), then release.
                var loops = new[] { _stdoutLoop, _stderrLoop }
                    .Where(t => t is not null).Cast<Task>().ToArray();
                if (loops.Length > 0)
                    await Task.WhenAny(Task.WhenAll(loops), Task.Delay(TimeSpan.FromSeconds(2)));
                process.Dispose();
            }
        }
        // Belt-and-braces: nothing may stay parked on a disposed transport.
        OnProcessGone();
        _lifecycleLock.Dispose();
        _writeLock.Dispose();
    }
}
