using System.Net;

namespace SranDesktop.Infrastructure.Api;

/// <summary>
/// Minimal exponential-backoff retry for transient HTTP failures
/// (network errors and 408/429/5xx). No external dependency.
/// </summary>
internal static class RetryHelper
{
    private static readonly HashSet<HttpStatusCode> TransientStatuses = new()
    {
        HttpStatusCode.RequestTimeout,        // 408
        (HttpStatusCode)429,                  // Too Many Requests
        HttpStatusCode.InternalServerError,   // 500
        HttpStatusCode.BadGateway,            // 502
        HttpStatusCode.ServiceUnavailable,    // 503
        HttpStatusCode.GatewayTimeout         // 504
    };

    public static async Task<T> WithRetryAsync<T>(
        Func<Task<T>> action,
        int maxRetries = 3,
        int baseDelayMs = 1000,
        CancellationToken ct = default)
    {
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                return await action();
            }
            catch (HttpRequestException ex) when (attempt < maxRetries && IsTransient(ex))
            {
                await Task.Delay(baseDelayMs * (1 << attempt), ct);
            }
            catch (TaskCanceledException) when (attempt < maxRetries && !ct.IsCancellationRequested)
            {
                // Per-request timeout (not user cancellation) — treat as transient.
                await Task.Delay(baseDelayMs * (1 << attempt), ct);
            }
        }
    }

    private static bool IsTransient(HttpRequestException ex) =>
        ex.StatusCode is null || TransientStatuses.Contains(ex.StatusCode.Value);
}
