using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Api;

/// <summary>
/// Broker client for the gateway's connector OAuth routes (Phase 7 C3).
/// The HttpClient is portal-JWT-authenticated (AuthTokenHandler pipeline)
/// against the gateway base URL — the same wiring as AgentKeyProvider.
/// Everything here is best-effort: a failure returns null/empty/false and
/// never throws into the UI.
/// </summary>
public sealed class ConnectorAuthClient : IConnectorAuthClient
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;
    private readonly ILogger<ConnectorAuthClient> _logger;

    public ConnectorAuthClient(HttpClient http, ILogger<ConnectorAuthClient> logger)
    {
        _http = http;
        _logger = logger;
    }

    public async Task<ConnectorTicket?> CreateTicketAsync(
        string connectorId, CancellationToken ct = default)
    {
        try
        {
            using var response = await _http.PostAsync(
                $"portal/connectors/{Uri.EscapeDataString(connectorId)}/ticket",
                content: null, ct);
            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning("Connect ticket for {Id} failed: HTTP {Status}",
                    connectorId, (int)response.StatusCode);
                return null;
            }
            return await response.Content.ReadFromJsonAsync<ConnectorTicket>(JsonOptions, ct);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            _logger.LogWarning(ex, "Connect ticket for {Id} failed", connectorId);
            return null;
        }
    }

    public async Task<IReadOnlyList<ConnectorConnection>> GetConnectionsAsync(
        CancellationToken ct = default)
    {
        try
        {
            using var response = await _http.GetAsync("portal/connectors", ct);
            if (!response.IsSuccessStatusCode)
                return Array.Empty<ConnectorConnection>();
            var body = await response.Content
                .ReadFromJsonAsync<ConnectionsResponse>(JsonOptions, ct);
            return body?.Connections ?? new List<ConnectorConnection>();
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            _logger.LogWarning(ex, "Connector connection listing failed");
            return Array.Empty<ConnectorConnection>();
        }
    }

    public async Task<bool> DisconnectAsync(string connectorId, CancellationToken ct = default)
    {
        try
        {
            using var response = await _http.DeleteAsync(
                $"portal/connectors/{Uri.EscapeDataString(connectorId)}", ct);
            return response.IsSuccessStatusCode;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogDebug(ex, "Server-side disconnect for {Id} failed", connectorId);
            return false;
        }
    }

    private sealed record ConnectionsResponse(
        [property: JsonPropertyName("connections")] List<ConnectorConnection>? Connections);
}
