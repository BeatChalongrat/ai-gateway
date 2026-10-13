using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.ToolsSidecar;

/// <summary>
/// MCP server over stdio: newline-delimited JSON-RPC 2.0, one message per
/// line — the framing <c>StdioMcpTransport</c> (the WPF client) and the
/// Electron host's transport both speak.
///
/// E4 (its record, §1):
///   * every <c>tools/call</c> must carry <c>_meta.grants</c> (the folders the
///     host granted, possibly empty) and <c>_meta.conversationId</c>; a call
///     without them is REFUSED before the tool is resolved — refusal is the
///     default, never "nothing configured, allow";
///   * grants are applied per call under a gate that serialises path-bearing
///     tools while the grant set is swapped;
///   * the conversation id is set on <see cref="ShellSession"/> inside the
///     per-request task so the AsyncLocal flows down the tool's async chain;
///   * <c>_meta.progressToken</c> turns a streaming tool's live output into
///     <c>notifications/progress</c> — MCP's own carrier, no side channel;
///   * <c>notifications/cancelled {requestId}</c> cancels the request's token.
/// </summary>
public sealed class StdioMcpServer
{
    private const string ProtocolVersion = "2025-06-18";

    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
    };

    private readonly IMcpToolHost _host;
    private readonly ShellSession _session;
    private readonly TextReader _input;
    private readonly TextWriter _output;
    private readonly ILogger _logger;
    private readonly Func<object> _status;
    /// <summary>FIX1 §1.3: the host-only control surface (settings.*, browser
    /// picker). Null keeps the server tools-only. NEVER enumerated by tools/list.</summary>
    private readonly HostQuerySurface? _hostQuery;
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    /// <summary>Serialises path-bearing tool calls while grants are swapped in.</summary>
    private readonly SemaphoreSlim _pathGate = new(1, 1);
    private readonly ConcurrentDictionary<string, CancellationTokenSource> _inFlight = new();

    public StdioMcpServer(IMcpToolHost host, ShellSession session, TextReader input, TextWriter output, ILogger logger, Func<object> status, HostQuerySurface? hostQuery = null)
    {
        _host = host;
        _session = session;
        _input = input;
        _output = output;
        _logger = logger;
        _status = status;
        _hostQuery = hostQuery;
    }

    public async Task RunAsync(CancellationToken ct)
    {
        var inFlight = new List<Task>();
        while (await _input.ReadLineAsync(ct) is { } line)
        {
            if (string.IsNullOrWhiteSpace(line))
                continue;

            JsonNode? node;
            try
            {
                node = JsonNode.Parse(line);
            }
            catch (JsonException ex)
            {
                _logger.LogWarning("Discarding non-JSON stdin line ({Length} chars): {Error}", line.Length, ex.Message);
                await WriteErrorAsync(null, -32700, "Parse error", ct);
                continue;
            }
            if (node is not JsonObject request)
            {
                await WriteErrorAsync(null, -32600, "Invalid Request", ct);
                continue;
            }

            var id = request["id"]?.DeepClone();
            var method = request["method"]?.GetValue<string>();
            var @params = request["params"];
            if (method is null)
                continue; // a response to a server→client request; we never send any
            if (id is null)
            {
                HandleNotification(method, @params);
                continue;
            }

            inFlight.RemoveAll(t => t.IsCompleted);
            inFlight.Add(Task.Run(() => HandleRequestAsync(id, method, @params, ct), ct));
        }
        _logger.LogInformation("stdin closed; draining {Count} in-flight request(s)", inFlight.Count(t => !t.IsCompleted));
        foreach (var cts in _inFlight.Values) cts.Cancel();
        await Task.WhenAny(Task.WhenAll(inFlight), Task.Delay(TimeSpan.FromSeconds(5), CancellationToken.None));
    }

    private void HandleNotification(string method, JsonNode? @params)
    {
        switch (method)
        {
            case "notifications/initialized":
                _logger.LogInformation("Client initialized");
                break;
            case "notifications/cancelled":
            {
                var requestId = @params?["requestId"]?.ToJsonString();
                if (requestId is not null && _inFlight.TryGetValue(requestId, out var cts))
                {
                    _logger.LogInformation("CANCEL request {Id}: {Reason}", requestId, @params?["reason"]?.GetValue<string>() ?? "-");
                    cts.Cancel();
                }
                else
                {
                    _logger.LogInformation("notifications/cancelled for unknown/finished request {Id}", requestId ?? "-");
                }
                break;
            }
            default:
                _logger.LogDebug("Ignoring notification {Method}", method);
                break;
        }
    }

    private async Task HandleRequestAsync(JsonNode id, string method, JsonNode? @params, CancellationToken ct)
    {
        var key = id.ToJsonString();
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        _inFlight[key] = cts;
        try
        {
            object? result = method switch
            {
                "initialize" => Initialize(),
                "ping" => new { },
                "tools/list" => ListTools(),
                "tools/call" => await CallToolAsync(@params, cts.Token),
                "sran/status" => _status(),
                "host/query" => await HostQueryAsync(@params),
                _ => null
            };
            if (result is null)
            {
                await WriteErrorAsync(id, -32601, $"Method not found: {method}", ct);
                return;
            }
            if (result is JsonRpcError err)
            {
                await WriteErrorAsync(id, err.Code, err.Message, ct);
                return;
            }
            await WriteAsync(new JsonObject { ["jsonrpc"] = "2.0", ["id"] = id, ["result"] = JsonSerializer.SerializeToNode(result, Json) }, ct);
        }
        catch (OperationCanceledException) when (cts.IsCancellationRequested && !ct.IsCancellationRequested)
        {
            await WriteErrorAsync(id, -32800, "Request cancelled", ct);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            // Shutting down.
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Unhandled error in {Method}", method);
            await WriteErrorAsync(id, -32603, $"Internal error: {ex.Message}", ct);
        }
        finally
        {
            _inFlight.TryRemove(key, out _);
        }
    }

    private object Initialize() => new
    {
        protocolVersion = ProtocolVersion,
        capabilities = new { tools = new { } },
        serverInfo = new { name = "SranDesktop.ToolsSidecar", version = SidecarInfo.Version },
        instructions = $"pid={Environment.ProcessId}"
    };

    private object ListTools() => new
    {
        tools = _host.GetToolDefinitions().Select(d => new
        {
            name = d.Function.Name,
            description = d.Function.Description,
            inputSchema = d.Function.Parameters
        }).ToArray()
    };

    /// <summary>FIX1 §1.3: the host-only control surface. Distinct from
    /// tools/call; never in tools/list. Returns the closed <c>{ok,data,error}</c>
    /// shape (never a host internal). A server with no surface answers method-not-
    /// found, so the model — which only ever calls tools/* — has no vocabulary here.</summary>
    private async Task<object> HostQueryAsync(JsonNode? @params)
    {
        if (_hostQuery is null)
            return new JsonRpcError(-32601, "Method not found: host/query");
        var op = @params?["op"]?.GetValue<string>();
        if (string.IsNullOrEmpty(op))
            return new JsonRpcError(-32602, "host/query requires params.op");
        var args = @params?["args"] as JsonObject;
        var (ok, data, error) = await _hostQuery.DispatchAsync(op, args);
        return new { ok, data, error };
    }

    private async Task<object> CallToolAsync(JsonNode? @params, CancellationToken ct)
    {
        var name = @params?["name"]?.GetValue<string>();
        if (string.IsNullOrEmpty(name))
            return new JsonRpcError(-32602, "tools/call requires params.name");

        // ── the E4 gate: no grants, no call ─────────────────────────────────
        var meta = @params?["_meta"] as JsonObject;
        if (meta is null)
            return new JsonRpcError(-32602, "grants-missing: tools/call must carry _meta.grants (the folders the user granted; an empty array is a valid answer) and _meta.conversationId. Refused.");
        if (meta["grants"] is not JsonArray grantsNode)
            return new JsonRpcError(-32602, "grants-missing: _meta.grants must be an array of absolute folder paths (empty = profile and temp only). Refused.");
        var grants = new List<string>();
        foreach (var g in grantsNode)
        {
            if (g is null || g.GetValueKind() != JsonValueKind.String)
                return new JsonRpcError(-32602, "grants-invalid: every _meta.grants entry must be a string. Refused.");
            grants.Add(g.GetValue<string>());
        }
        var conversationId = meta["conversationId"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(conversationId))
            return new JsonRpcError(-32602, "conversationId-missing: tools/call must carry _meta.conversationId. Refused.");
        var progressToken = meta["progressToken"]?.DeepClone();

        var tool = _host.Resolve(name);
        if (tool is null)
            return new JsonRpcError(-32602, $"Unknown tool: {name}");

        var argsNode = @params?["arguments"];
        var arguments = argsNode is null ? JsonDocument.Parse("{}").RootElement : JsonSerializer.SerializeToElement(argsNode, Json);

        // The AsyncLocal is set INSIDE this request's task: it flows into the
        // tool's async chain, exactly as the orchestrator's assignment did.
        _session.CurrentConversationId = conversationId;
        // FIX6: the origin the host authorized for this browser action (optional;
        // present only for gated browser tools). A tab-acting browser tool
        // re-checks the live tab origin against it before acting.
        _session.CurrentAuthorizedSite = meta["authorizedSite"]?.GetValue<string>();

        // FIX6-addendum: the per-site gate is fail-CLOSED, symmetric with the
        // grants gate above (E4 §1 #4). A tab-acting browser tool (data:
        // IBrowserTabAction) with no authorized origin is REFUSED here, before it
        // runs — because this layer exists to catch a HOST bug, and a host bug is
        // exactly when the origin is missing. navigate/open_url/list_tabs are not in
        // the family, so they are unaffected.
        if (BrowserSiteGate.RequiresRefusal(tool, _session.CurrentAuthorizedSite))
        {
            _logger.LogWarning("tools/call {Tool} REFUSED: {Msg}", name, BrowserSiteGate.MissingSiteMessage);
            return new JsonRpcError(-32602, BrowserSiteGate.MissingSiteMessage);
        }

        // Live output → notifications/progress, correlated by the caller's token.
        Action<ToolOutputChunk>? onChunk = null;
        var streaming = tool as ISupportsOutputStreaming;
        if (streaming is not null && progressToken is not null)
        {
            var n = 0;
            onChunk = chunk =>
            {
                if (chunk.ToolName != tool.Name) return;
                var p = Interlocked.Increment(ref n);
                _ = WriteAsync(new JsonObject
                {
                    ["jsonrpc"] = "2.0",
                    ["method"] = "notifications/progress",
                    ["params"] = new JsonObject { ["progressToken"] = progressToken.DeepClone(), ["progress"] = p, ["message"] = chunk.Text }
                }, CancellationToken.None);
            };
            streaming.OutputStreamed += onChunk;
        }

        var pathBearing = tool.Category is ToolCategory.FileSystem or ToolCategory.Shell;
        _logger.LogInformation("tools/call {Tool} conv={Conv} grants={Grants}{Gate}", name, conversationId, grants.Count, pathBearing ? " [path gate]" : "");
        ToolResult result;
        if (pathBearing) await _pathGate.WaitAsync(ct);
        try
        {
            // Applied per call, validated per entry (FolderGrantValidator drops
            // forbidden roots). The built-in roots stay what they are in the WPF app.
            var applied = PathGuard.ConfigureGrantedRoots(grants);
            if (applied.Count != grants.Count)
                _logger.LogWarning("tools/call {Tool}: {Dropped} grant(s) rejected by FolderGrantValidator", name, grants.Count - applied.Count);
            try
            {
                result = await tool.ExecuteAsync(arguments, ct);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                result = ToolResult.Fail($"{tool.Name} threw {ex.GetType().Name}: {ex.Message}");
            }
        }
        finally
        {
            if (pathBearing) _pathGate.Release();
            if (onChunk is not null && streaming is not null) streaming.OutputStreamed -= onChunk;
        }

        var content = new List<object> { new { type = "text", text = result.Success ? result.TruncatedContent : (result.Error ?? "Tool failed") } };
        if (result.ImageDataUri is { } dataUri)
        {
            var comma = dataUri.IndexOf(',');
            var header = dataUri[..comma];
            var mime = header["data:".Length..header.IndexOf(';')];
            content.Add(new { type = "image", data = dataUri[(comma + 1)..], mimeType = mime });
        }
        return new { content, isError = !result.Success };
    }

    private Task WriteErrorAsync(JsonNode? id, int code, string message, CancellationToken ct) =>
        WriteAsync(new JsonObject { ["jsonrpc"] = "2.0", ["id"] = id?.DeepClone(), ["error"] = new JsonObject { ["code"] = code, ["message"] = message } }, ct);

    private async Task WriteAsync(JsonObject envelope, CancellationToken ct)
    {
        var text = envelope.ToJsonString(Json);
        await _writeLock.WaitAsync(ct);
        try
        {
            await _output.WriteAsync(text.AsMemory(), ct);
            await _output.WriteAsync("\n".AsMemory(), ct);
            await _output.FlushAsync(ct);
        }
        finally
        {
            _writeLock.Release();
        }
    }

    private sealed record JsonRpcError(int Code, string Message);
}

public static class SidecarInfo
{
    public const string Version = "0.2.0-e4";
}
