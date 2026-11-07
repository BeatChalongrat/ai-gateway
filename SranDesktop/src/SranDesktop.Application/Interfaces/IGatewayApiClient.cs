using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Client for the gateway's OpenAI-compatible /v1/chat/completions.
/// Authenticated with a tenant gateway API key (gw_…), NOT the portal JWT —
/// the completions endpoint rejects portal tokens (verified live).
/// </summary>
public interface IGatewayApiClient
{
    /// <summary>Streams a completion; yields raw SSE frames (data-only, no event names).</summary>
    IAsyncEnumerable<SseFrame> StreamCompletionAsync(
        GatewayCompletionRequest request, CancellationToken ct = default);

    /// <summary>POST /v1/search — the gateway fans out to its configured search
    /// providers (Tavily/Brave/SerpAPI/...). Throws on HTTP failure (R9 C).</summary>
    Task<GatewaySearchResponse> SearchAsync(
        string query, int maxResults = 5, CancellationToken ct = default);
}
