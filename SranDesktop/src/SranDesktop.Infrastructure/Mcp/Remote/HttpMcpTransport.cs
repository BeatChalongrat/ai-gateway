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
    private readonly string? _authHeader;
    private string? _sessionId;
    private long _nextId;

    /// <summary>Phase 7 C4: the Bearer is resolved PER REQUEST so an oauth2
    /// connector always sends the CURRENT portal access token — a mid-session
    /// JWT refresh is picked up without re-dialing the client.
    /// <paramref name="authHeader"/> (custom-connector UI): null/"Authorization"
    /// keeps the Bearer scheme; any other name sends the RAW token under that
    /// header (API-key servers like X-Api-Key expect no Bearer prefix).</summary>
    public HttpMcpTransport(string baseUrl, Func<string?>? tokenProvider, HttpClient? http = null,
        string? authHeader = null, int? maximumResponseBytes = null)
    {
        _baseUrl = baseUrl.TrimEnd('/');
        _ownsHttp = http is null;
        _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        _tokenProvider = tokenProvider;
        _authHeader = authHeader;
        _maximumResponseBytes = maximumResponseBytes is > 0
            ? maximumResponseBytes.Value
            : DefaultMaximumResponseBytes;
    }

    /// <summary>
    /// Largest response this transport will read.
    ///
    /// A server on the other end of a socket decides how many bytes to send, and
    /// <c>ReadAsStringAsync</c> will faithfully allocate all of them. A connector
    /// that answers tools/list with a gigabyte is not a protocol error to be
    /// parsed — it is a denial of service to be cut off.
    /// </summary>
    public const int DefaultMaximumResponseBytes = 8 * 1024 * 1024;

    private readonly int _maximumResponseBytes;

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

        using var httpResponse = await _http.SendAsync(
            httpRequest, HttpCompletionOption.ResponseHeadersRead, ct);

        // 401/403 is not a transport fault to be retried — it means the account
        // needs reconnecting, and saying so lets the caller surface that instead
        // of a generic failure the user cannot act on. The response body is NOT
        // included: a server rejecting a credential frequently echoes it.
        if (httpResponse.StatusCode is System.Net.HttpStatusCode.Unauthorized
            or System.Net.HttpStatusCode.Forbidden)
            throw new McpAuthorizationException(
                $"The connector refused the credential ({(int)httpResponse.StatusCode}). "
                + "The connected account may need to be reconnected.");
        httpResponse.EnsureSuccessStatusCode();

        if (httpResponse.Headers.TryGetValues("Mcp-Session-Id", out var sessionValues))
            _sessionId = sessionValues.FirstOrDefault() ?? _sessionId;

        if (isNotification)
            return new JsonRpcResponse();

        var body = await ReadBoundedAsync(httpResponse, ct);
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
        var credential = _tokenProvider?.Invoke();
        if (string.IsNullOrEmpty(credential))
            return;
        var name = string.IsNullOrEmpty(_authHeader) ? "Authorization" : _authHeader;

        // Two kinds of value arrive here. A legacy api_key path yields a bare
        // token and expects the Bearer scheme to be added. The credential broker
        // yields a COMPLETE header value, because the scheme is part of what the
        // provider config declared and therefore part of what was approved.
        // Telling them apart by the space is reliable — a bare credential does
        // not contain one — and getting it wrong produces "Bearer Bearer …",
        // which every server rejects with a 401 that looks like a bad token.
        if (name.Equals("Authorization", StringComparison.OrdinalIgnoreCase)
            && !credential.Contains(' '))
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", credential);
        else
            request.Headers.TryAddWithoutValidation(name, credential);
    }

    /// <summary>
    /// Reads at most <see cref="_maximumResponseBytes"/>, and refuses rather than
    /// truncating: a JSON document cut in half parses as garbage, and reporting
    /// "malformed response" for what is actually "too large" sends whoever
    /// debugs it in the wrong direction entirely.
    /// </summary>
    private async Task<string> ReadBoundedAsync(
        HttpResponseMessage response, CancellationToken ct)
    {
        if (response.Content.Headers.ContentLength is { } declared
            && declared > _maximumResponseBytes)
            throw new McpProtocolException(
                $"The connector's response declares {declared} bytes; the limit is "
                + $"{_maximumResponseBytes}.");

        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        var buffer = new byte[_maximumResponseBytes];
        var total = 0;
        while (total < buffer.Length)
        {
            var read = await stream.ReadAsync(buffer.AsMemory(total), ct);
            if (read == 0)
                return Encoding.UTF8.GetString(buffer, 0, total);
            total += read;
        }

        // The buffer filled and the stream still has more.
        if (await stream.ReadAsync(new byte[1].AsMemory(), ct) > 0)
            throw new McpProtocolException(
                $"The connector's response exceeded {_maximumResponseBytes} bytes.");
        return Encoding.UTF8.GetString(buffer, 0, total);
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
