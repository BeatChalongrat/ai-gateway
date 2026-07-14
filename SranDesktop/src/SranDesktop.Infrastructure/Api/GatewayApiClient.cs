using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Api;

/// <summary>
/// Streams OpenAI-compatible completions from the gateway (:8080/v1).
/// Authenticated with the self-provisioned gw_… agent API key — the portal
/// JWT is rejected by this endpoint (verified live), so this client does NOT
/// use AuthTokenHandler.
///
/// Resilience: a 401 triggers ONE key re-provision + retry (revoked/rotated
/// key); a connection failure or 429/5xx before any frame triggers ONE
/// delayed retry (Retry-After honored on 429); an idle watchdog aborts a
/// stream that sends nothing for <see cref="IdleTimeoutSeconds"/> seconds.
/// Never retries once streaming has begun — partial output must not repeat.
/// </summary>
public sealed class GatewayApiClient : IGatewayApiClient
{
    internal static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private const int IdleTimeoutSeconds = 90;
    private const int TransientRetryDelaySeconds = 2;
    private const int MaxRetryAfterSeconds = 30;

    private readonly HttpClient _http;
    private readonly IAgentKeyProvider _keyProvider;
    private readonly ILogger<GatewayApiClient> _logger;
    private readonly TimeSpan _idleTimeout;
    private readonly Func<TimeSpan, CancellationToken, Task> _delay;

    public GatewayApiClient(
        HttpClient http,
        IAgentKeyProvider keyProvider,
        ILogger<GatewayApiClient> logger,
        TimeSpan? idleTimeout = null,
        Func<TimeSpan, CancellationToken, Task>? retryDelay = null)
    {
        _http = http;
        _keyProvider = keyProvider;
        _logger = logger;
        _idleTimeout = idleTimeout ?? TimeSpan.FromSeconds(IdleTimeoutSeconds);
        _delay = retryDelay ?? Task.Delay;
    }

    public async IAsyncEnumerable<SseFrame> StreamCompletionAsync(
        GatewayCompletionRequest request,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var apiKey = await _keyProvider.GetOrProvisionKeyAsync(ct);
        if (apiKey is null)
        {
            yield return new SseFrame(null,
                """{"error":{"code":"no_agent_key","message":"Could not obtain a gateway API key for Agent Mode. Check that you are signed in and the gateway is reachable."}}""");
            yield break;
        }

        var authRetried = false;
        var transientRetried = false;

        while (true)
        {
            HttpResponseMessage? response = null;
            string? connectionError = null;
            using (var httpRequest = BuildCompletionRequest(request, apiKey))
            {
                try
                {
                    response = await _http.SendAsync(
                        httpRequest, HttpCompletionOption.ResponseHeadersRead, ct);
                }
                catch (Exception ex) when (ex is HttpRequestException or IOException && !ct.IsCancellationRequested)
                {
                    _logger.LogWarning(ex, "Gateway unreachable for agent completion");
                    connectionError =
                        "{\"error\":{\"code\":\"gateway_unreachable\",\"message\":\"Could not reach the gateway. " +
                        "Agent Mode requires the gateway - check the Gateway URL in Settings, or turn off Agent Mode " +
                        "(the robot button) to use standard chat.\"}}";
                }
            }

            if (connectionError is not null)
            {
                if (!transientRetried)
                {
                    transientRetried = true;
                    _logger.LogWarning("Retrying agent completion once in {Delay}s after a connection failure",
                        TransientRetryDelaySeconds);
                    await _delay(TimeSpan.FromSeconds(TransientRetryDelaySeconds), ct);
                    continue;
                }
                yield return new SseFrame(null, connectionError);
                yield break;
            }

            // Revoked/rotated key — mint a fresh one and retry once.
            if (response!.StatusCode == HttpStatusCode.Unauthorized && !authRetried)
            {
                authRetried = true;
                response.Dispose();
                _logger.LogWarning("Agent key rejected by the gateway (401), re-provisioning");
                var freshKey = await _keyProvider.ReprovisionAsync(ct);
                if (freshKey is null)
                {
                    yield return new SseFrame(null,
                        """{"error":{"code":"agent_key_reprovision_failed","message":"The gateway rejected the agent API key and a replacement could not be provisioned. Check that you are signed in."}}""");
                    yield break;
                }
                apiKey = freshKey;
                continue;
            }

            // 429/5xx before any frame — retry once (Retry-After honored on 429).
            var status = (int)response.StatusCode;
            if ((status == 429 || status >= 500) && !transientRetried)
            {
                transientRetried = true;
                var delay = GetTransientRetryDelay(response);
                response.Dispose();
                _logger.LogWarning("Gateway completion failed: HTTP {Status}, retrying once in {Delay}s",
                    status, delay.TotalSeconds);
                await _delay(delay, ct);
                continue;
            }

            try
            {
                if (!response.IsSuccessStatusCode)
                {
                    var body = await response.Content.ReadAsStringAsync(ct);
                    _logger.LogWarning("Gateway completion failed: HTTP {Status} {Body}",
                        (int)response.StatusCode, Truncate(body, 300));
                    yield return new SseFrame(null, body.Length > 0
                        ? body
                        : "{\"error\":{\"code\":\"http_" + (int)response.StatusCode +
                          "\",\"message\":\"Gateway request failed\"}}");
                    yield break;
                }

                // Non-SSE 200 (the stack wraps some auth errors as plain-JSON 200s).
                if (response.Content.Headers.ContentType?.MediaType is { } mediaType
                    && !mediaType.Contains("event-stream", StringComparison.OrdinalIgnoreCase))
                {
                    var body = await response.Content.ReadAsStringAsync(ct);
                    yield return new SseFrame(null, body);
                    yield break;
                }

                // Idle watchdog: a server that keeps the socket open but sends
                // nothing (or only SSE keep-alive comments, which never surface
                // as frames) must not hang the turn forever.
                using var watchdog = CancellationTokenSource.CreateLinkedTokenSource(ct);
                watchdog.CancelAfter(_idleTimeout);

                await using var stream = await response.Content.ReadAsStreamAsync(ct);
                await using var frames = SseStreamReader.ParseAsync(stream, watchdog.Token).GetAsyncEnumerator();
                while (true)
                {
                    var moved = false;
                    var stalled = false;
                    try
                    {
                        moved = await frames.MoveNextAsync();
                    }
                    catch (Exception ex) when (ex is OperationCanceledException or IOException
                                               && watchdog.IsCancellationRequested
                                               && !ct.IsCancellationRequested)
                    {
                        stalled = true; // watchdog fired, not the user — yield below (not allowed in catch)
                    }

                    if (stalled)
                    {
                        _logger.LogWarning("Gateway stream stalled: no data for {Seconds}s, aborting",
                            _idleTimeout.TotalSeconds);
                        yield return new SseFrame(null,
                            "{\"error\":{\"code\":\"stream_stalled\",\"message\":\"The gateway connection stalled (no data for " +
                            (int)_idleTimeout.TotalSeconds + " seconds). Please try again.\"}}");
                        yield break;
                    }
                    if (!moved)
                        yield break;

                    watchdog.CancelAfter(_idleTimeout); // reset on every frame
                    yield return frames.Current;
                }
            }
            finally
            {
                response.Dispose();
            }
        }
    }

    public async Task<GatewaySearchResponse> SearchAsync(
        string query, int maxResults = 5, CancellationToken ct = default)
    {
        var apiKey = await _keyProvider.GetOrProvisionKeyAsync(ct)
            ?? throw new HttpRequestException("Could not obtain a gateway API key for web search.");

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(TimeSpan.FromSeconds(30));

        var response = await SendSearchRequestAsync(query, maxResults, apiKey, cts.Token);
        try
        {
            // Revoked/rotated key — mint a fresh one and retry once.
            if (response.StatusCode == HttpStatusCode.Unauthorized)
            {
                response.Dispose();
                _logger.LogWarning("Agent key rejected by the gateway (401), re-provisioning");
                apiKey = await _keyProvider.ReprovisionAsync(cts.Token)
                    ?? throw new HttpRequestException(
                        "Web search failed: the gateway rejected the agent API key and a replacement " +
                        "could not be provisioned. Check that you are signed in.",
                        null, HttpStatusCode.Unauthorized);
                response = await SendSearchRequestAsync(query, maxResults, apiKey, cts.Token);
            }

            if (!response.IsSuccessStatusCode)
            {
                var body = await response.Content.ReadAsStringAsync(cts.Token);
                throw new HttpRequestException(
                    $"Web search failed (HTTP {(int)response.StatusCode}): {Truncate(body, 300)}",
                    null, response.StatusCode);
            }

            return await response.Content.ReadFromJsonAsync<GatewaySearchResponse>(JsonOptions, cts.Token)
                   ?? throw new HttpRequestException("Web search returned an empty body");
        }
        finally
        {
            response.Dispose();
        }
    }

    private static HttpRequestMessage BuildCompletionRequest(GatewayCompletionRequest request, string apiKey)
    {
        // A request message cannot be reused after sending — retries rebuild it.
        var httpRequest = new HttpRequestMessage(HttpMethod.Post, "v1/chat/completions")
        {
            Content = JsonContent.Create(request, options: JsonOptions)
        };
        httpRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        httpRequest.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
        return httpRequest;
    }

    private async Task<HttpResponseMessage> SendSearchRequestAsync(
        string query, int maxResults, string apiKey, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, "v1/search")
        {
            Content = JsonContent.Create(new GatewaySearchRequest(query, maxResults), options: JsonOptions)
        };
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        return await _http.SendAsync(request, ct);
    }

    private static TimeSpan GetTransientRetryDelay(HttpResponseMessage response)
    {
        if (response.StatusCode == HttpStatusCode.TooManyRequests
            && response.Headers.RetryAfter is { } retryAfter)
        {
            var delay = retryAfter.Delta
                        ?? (retryAfter.Date is { } date ? date - DateTimeOffset.UtcNow : (TimeSpan?)null);
            if (delay > TimeSpan.Zero)
                return delay.Value <= TimeSpan.FromSeconds(MaxRetryAfterSeconds)
                    ? delay.Value
                    : TimeSpan.FromSeconds(MaxRetryAfterSeconds);
        }
        return TimeSpan.FromSeconds(TransientRetryDelaySeconds);
    }

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max] + "...";
}
