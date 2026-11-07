using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

// Wire shapes of the gateway's POST /v1/search (R9 Feature C).

public sealed record GatewaySearchRequest(
    [property: JsonPropertyName("query")] string Query,
    [property: JsonPropertyName("max_results")] int MaxResults = 5);

public sealed record GatewaySearchResultItem(
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("url")] string Url,
    [property: JsonPropertyName("snippet")] string? Snippet,
    [property: JsonPropertyName("domain")] string? Domain,
    [property: JsonPropertyName("published_date")] string? PublishedDate);

public sealed record GatewaySearchResponse(
    [property: JsonPropertyName("query")] string Query,
    [property: JsonPropertyName("provider")] string? Provider,
    [property: JsonPropertyName("latency_ms")] int LatencyMs,
    [property: JsonPropertyName("results")] List<GatewaySearchResultItem> Results);

/// <summary>Session-scoped web-search toggle state, shared by the composer
/// toggle and the tool host filter (R9 C3).</summary>
public static class WebSearchState
{
    public static bool Enabled { get; set; }
}
