using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// JSON-RPC 2.0 over streamable HTTP (R10 Fix 1; hoisted verbatim out of
/// McpRemoteClient behind <see cref="IMcpTransport"/> in Phase 12 D —
/// behavior-identical). Spec: https://modelcontextprotocol.io/specification —
/// POST {base}/mcp with an Mcp-Session-Id header once the server assigns one;
/// responses may arrive as plain JSON or as a single SSE event stream.
/// </summary>
public sealed class HttpMcpTransport : IMcpTransport
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

    /// <summary>Phase 7 C4: the Bearer is resolved PER REQUEST so an oauth2
    /// connector always sends the CURRENT portal access token — a mid-session
    /// JWT refresh is picked up without re-dialing the client.</summary>
    public HttpMcpTransport(string baseUrl, Func<string?>? tokenProvider, HttpClient? http = null)
    {
        _baseUrl = baseUrl.TrimEnd('/');
        _ownsHttp = http is null;
        _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        _tokenProvider = tokenProvider;
    }

    public string? SessionId => _sessionId;

    /// <summary>HTTP is connectionless per request — nothing to start.</summary>
    public Task EnsureStartedAsync(CancellationToken ct = default) => Task.CompletedTask;

    /// <summary>Never raised: there is no long-lived local process to die;
    /// HTTP failures surface per call, exactly as before this refactor.</summary>
    public event EventHandler? Exited { add { } remove { } }

    public Task<JsonRpcResponse> SendRequestAsync(string method, object? @params, CancellationToken ct = default) =>
        PostRpcAsync(method, @params, isNotification: false, ct);

    public Task SendNotificationAsync(string method, object? @params, CancellationToken ct = default) =>
        PostRpcAsync(method, @params, isNotification: true, ct);

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
}
