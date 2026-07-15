using System.Text;
using System.Text.Json;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// MCP protocol client (R10 Fix 1). Phase 12 D: the JSON-RPC plumbing moved
/// behind <see cref="IMcpTransport"/> so the same protocol layer runs over
/// streamable HTTP (<see cref="HttpMcpTransport"/>) or a local stdio server
/// process (<see cref="StdioMcpTransport"/>). The HTTP-flavored constructors
/// are preserved verbatim for existing call sites and tests.
/// </summary>
public sealed class McpRemoteClient : IAsyncDisposable
{
    private readonly IMcpTransport _transport;

    /// <summary>Exposed for tests / reuse of a mocked handler.</summary>
    public McpRemoteClient(string baseUrl, string? authToken = null, HttpClient? http = null)
        : this(baseUrl, authToken is null ? null : new Func<string?>(() => authToken), http)
    {
    }

    /// <summary>Phase 7 C4: the Bearer is resolved PER REQUEST so an oauth2
    /// connector always sends the CURRENT portal access token — a mid-session
    /// JWT refresh is picked up without re-dialing the client.</summary>
    public McpRemoteClient(string baseUrl, Func<string?>? tokenProvider, HttpClient? http = null)
        : this(new HttpMcpTransport(baseUrl, tokenProvider, http))
    {
    }

    /// <summary>Phase 12 D: protocol layer over an explicit transport.</summary>
    public McpRemoteClient(IMcpTransport transport) => _transport = transport;

    /// <summary>The underlying transport — test seam.</summary>
    internal IMcpTransport Transport => _transport;

    /// <summary>HTTP session id, when the transport has one (stdio has none).</summary>
    public string? SessionId => (_transport as HttpMcpTransport)?.SessionId;

    /// <summary>Handshake: initialize + notifications/initialized. Call once.</summary>
    public async Task<McpInitResult> InitializeAsync(CancellationToken ct = default)
    {
        await _transport.EnsureStartedAsync(ct);

        var response = await _transport.SendRequestAsync("initialize", new
        {
            protocolVersion = "2025-06-18",
            capabilities = new { },
            clientInfo = new { name = "SranDesktop", version = "1.0.0" }
        }, ct);

        if (response.Error is not null)
            throw new McpProtocolException($"initialize failed: {response.Error}");

        await _transport.SendNotificationAsync("notifications/initialized", null, ct);

        var serverName = "Unknown";
        if (response.Result is { } result
            && result.TryGetProperty("serverInfo", out var info)
            && info.TryGetProperty("name", out var name))
            serverName = name.GetString() ?? serverName;

        return new McpInitResult { ServerName = serverName };
    }

    /// <summary>tools/list → tool definitions with JSON-schema inputs.</summary>
    public async Task<List<McpToolDefinition>> ListToolsAsync(CancellationToken ct = default)
    {
        var response = await _transport.SendRequestAsync("tools/list", null, ct);
        if (response.Error is not null)
            throw new McpProtocolException($"tools/list failed: {response.Error}");

        var tools = new List<McpToolDefinition>();
        if (response.Result is { } result
            && result.TryGetProperty("tools", out var array)
            && array.ValueKind == JsonValueKind.Array)
        {
            foreach (var tool in array.EnumerateArray())
            {
                tools.Add(new McpToolDefinition
                {
                    Name = tool.GetProperty("name").GetString() ?? "",
                    Description = tool.TryGetProperty("description", out var d) ? d.GetString() : null,
                    InputSchema = tool.TryGetProperty("inputSchema", out var s)
                        ? s.GetRawText() : "{\"type\":\"object\"}"
                });
            }
        }
        return tools;
    }

    /// <summary>tools/call — arguments as raw JSON element (passed verbatim).</summary>
    public async Task<McpToolResult> CallToolAsync(
        string toolName, JsonElement arguments, CancellationToken ct = default)
    {
        var response = await _transport.SendRequestAsync("tools/call",
            new { name = toolName, arguments }, ct);

        if (response.Error is not null)
            return new McpToolResult { Content = response.Error.ToString()!, IsError = true };

        var isError = false;
        var text = new StringBuilder();
        if (response.Result is { } result)
        {
            if (result.TryGetProperty("isError", out var err) && err.ValueKind == JsonValueKind.True)
                isError = true;
            // MCP content: [{type:"text", text:"..."}, ...]
            if (result.TryGetProperty("content", out var content)
                && content.ValueKind == JsonValueKind.Array)
            {
                foreach (var block in content.EnumerateArray())
                {
                    if (block.TryGetProperty("text", out var t))
                        text.AppendLine(t.GetString());
                    else
                        text.AppendLine(block.GetRawText());
                }
            }
            else
            {
                text.Append(result.GetRawText());
            }
        }
        return new McpToolResult { Content = text.ToString().TrimEnd(), IsError = isError };
    }

    /// <summary>Kept here for existing tests; implementation lives with the
    /// HTTP transport it belongs to.</summary>
    internal static string ExtractSseData(string sseBody) =>
        HttpMcpTransport.ExtractSseData(sseBody);

    public ValueTask DisposeAsync() => _transport.DisposeAsync();
}

public sealed class McpInitResult
{
    public string ServerName { get; set; } = "";
}

public sealed class McpToolDefinition
{
    public string Name { get; set; } = "";
    public string? Description { get; set; }
    public string InputSchema { get; set; } = "{}";
}

public sealed class McpToolResult
{
    public string Content { get; set; } = "";
    public bool IsError { get; set; }
}

public sealed class McpProtocolException : Exception
{
    public McpProtocolException(string message) : base(message)
    {
    }
}
