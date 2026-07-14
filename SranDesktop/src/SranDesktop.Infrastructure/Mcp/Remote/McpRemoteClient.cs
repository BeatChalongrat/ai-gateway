using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// MCP client speaking JSON-RPC 2.0 over streamable HTTP (R10 Fix 1).
/// Spec: https://modelcontextprotocol.io/specification — POST {base}/mcp with
/// an Mcp-Session-Id header once the server assigns one; responses may arrive
/// as plain JSON or as a single SSE event stream.
/// </summary>
public sealed class McpRemoteClient : IAsyncDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    private readonly HttpClient _http;
    private readonly bool _ownsHttp;
    private readonly string _baseUrl;
    private readonly Func<string?>? _tokenProvider;
    private string? _sessionId;
    private long _nextId;

    /// <summary>Exposed for tests / reuse of a mocked handler.</summary>
    public McpRemoteClient(string baseUrl, string? authToken = null, HttpClient? http = null)
        : this(baseUrl, authToken is null ? null : new Func<string?>(() => authToken), http)
    {
    }

    /// <summary>Phase 7 C4: the Bearer is resolved PER REQUEST so an oauth2
    /// connector always sends the CURRENT portal access token — a mid-session
    /// JWT refresh is picked up without re-dialing the client.</summary>
    public McpRemoteClient(string baseUrl, Func<string?>? tokenProvider, HttpClient? http = null)
    {
        _baseUrl = baseUrl.TrimEnd('/');
        _ownsHttp = http is null;
        _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        _tokenProvider = tokenProvider;
    }

    public string? SessionId => _sessionId;

    /// <summary>Handshake: initialize + notifications/initialized. Call once.</summary>
    public async Task<McpInitResult> InitializeAsync(CancellationToken ct = default)
    {
        var response = await PostRpcAsync("initialize", new
        {
            protocolVersion = "2025-06-18",
            capabilities = new { },
            clientInfo = new { name = "SranDesktop", version = "1.0.0" }
        }, isNotification: false, ct);

        if (response.Error is not null)
            throw new McpProtocolException($"initialize failed: {response.Error}");

        await PostRpcAsync("notifications/initialized", null, isNotification: true, ct);

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
        var response = await PostRpcAsync("tools/list", null, isNotification: false, ct);
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
        var response = await PostRpcAsync("tools/call",
            new { name = toolName, arguments }, isNotification: false, ct);

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

    private async Task<JsonRpcResponse> PostRpcAsync(
        string method, object? @params, bool isNotification, CancellationToken ct)
    {
        var payload = new Dictionary<string, object?>
        {
            ["jsonrpc"] = "2.0",
            ["method"] = method
        };
        if (!isNotification)
            payload["id"] = Interlocked.Increment(ref _nextId);
        if (@params is not null)
            payload["params"] = @params;

        using var httpRequest = new HttpRequestMessage(HttpMethod.Post, $"{_baseUrl}/mcp")
        {
            Content = new StringContent(
                JsonSerializer.Serialize(payload, JsonOptions), Encoding.UTF8, "application/json")
        };
        httpRequest.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        httpRequest.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
        ApplyAuth(httpRequest);
        if (_sessionId is not null)
            httpRequest.Headers.TryAddWithoutValidation("Mcp-Session-Id", _sessionId);

        using var httpResponse = await _http.SendAsync(httpRequest, ct);
        httpResponse.EnsureSuccessStatusCode();

        if (httpResponse.Headers.TryGetValues("Mcp-Session-Id", out var sessionValues))
            _sessionId = sessionValues.FirstOrDefault() ?? _sessionId;

        if (isNotification)
            return new JsonRpcResponse();

        var body = await httpResponse.Content.ReadAsStringAsync(ct);
        var mediaType = httpResponse.Content.Headers.ContentType?.MediaType ?? "";
        if (mediaType.Contains("event-stream", StringComparison.OrdinalIgnoreCase))
            body = ExtractSseData(body);

        using var doc = JsonDocument.Parse(body);
        return new JsonRpcResponse
        {
            Result = doc.RootElement.TryGetProperty("result", out var result)
                ? result.Clone() : null,
            Error = doc.RootElement.TryGetProperty("error", out var error)
                ? error.Clone() : null
        };
    }

    private void ApplyAuth(HttpRequestMessage request)
    {
        var token = _tokenProvider?.Invoke();
        if (!string.IsNullOrEmpty(token))
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
    }

    /// <summary>Streamable-HTTP servers may answer a POST with one SSE event —
    /// take the last data: line's JSON payload.</summary>
    internal static string ExtractSseData(string sseBody)
    {
        string? last = null;
        foreach (var line in sseBody.Split('\n'))
        {
            var trimmed = line.TrimEnd('\r');
            if (trimmed.StartsWith("data:", StringComparison.Ordinal))
                last = trimmed[5..].TrimStart();
        }
        return last ?? sseBody;
    }

    public async ValueTask DisposeAsync()
    {
        if (_sessionId is not null)
        {
            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Delete, $"{_baseUrl}/mcp");
                ApplyAuth(request);
                request.Headers.TryAddWithoutValidation("Mcp-Session-Id", _sessionId);
                using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await _http.SendAsync(request, cts.Token);
            }
            catch
            {
                // Best-effort session close.
            }
        }
        if (_ownsHttp)
            _http.Dispose();
    }

    private sealed class JsonRpcResponse
    {
        public JsonElement? Result { get; init; }
        public JsonElement? Error { get; init; }
    }
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
