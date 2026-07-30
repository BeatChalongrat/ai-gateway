using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// web_search — proxies to the gateway's POST /v1/search (Tavily/Brave/SerpAPI
/// fallback chain server-side). Listed only while the composer's web-search
/// toggle is on (R9 Feature C).
/// </summary>
public sealed class WebSearchTool : IMcpTool
{
    private readonly IGatewayApiClient _gateway;
    private readonly ILogger<WebSearchTool> _logger;

    public WebSearchTool(IGatewayApiClient gateway, ILogger<WebSearchTool> logger)
    {
        _gateway = gateway;
        _logger = logger;
    }

    public string Name => "web_search";

    public string Description =>
        "Search the web for current information. Use when asked about recent events, " +
        "current data, prices, news, or anything that may have changed since training.";

    public ToolCategory Category => ToolCategory.Network;

    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways; // read-only

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "query": { "type": "string", "description": "Search query" }
            },
            "required": ["query"]
        }
        """);

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        if (!WebSearchState.Enabled)
            return ToolResult.Fail("Web search is turned off. Ask the user to enable the web-search toggle.");

        if (!arguments.TryGetProperty("query", out var queryProp)
            || queryProp.GetString() is not { Length: > 0 } query)
            return ToolResult.Fail("Missing required argument: query");

        try
        {
            var response = await _gateway.SearchAsync(query, maxResults: 5, ct);
            var results = response.Results.Select(r => new
            {
                title = r.Title,
                url = r.Url,
                snippet = r.Snippet,
                domain = r.Domain,
                published_date = r.PublishedDate
            });
            // Search hits (titles/snippets/urls) are untrusted external content —
            // wrap so a poisoned result can't be read as instructions. The query
            // and provider are our own metadata but travel inside the same payload.
            var payload = JsonSerializer.Serialize(new
            {
                query = response.Query,
                provider = response.Provider,
                results
            });
            return ToolResult.Ok(UntrustedObservationBoundary.ProtectIfUntrusted(Name, payload));
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "web_search failed for {Query}", query);
            return ToolResult.Fail($"Web search failed: {ex.Message}");
        }
    }
}
