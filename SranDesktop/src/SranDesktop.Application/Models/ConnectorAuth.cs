using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>``POST /portal/connectors/{id}/ticket`` response — the one-time
/// ticket plus the browser URL that consumes it (Phase 7 C3).</summary>
public sealed class ConnectorTicket
{
    [JsonPropertyName("ticket")]
    public string Ticket { get; set; } = "";

    [JsonPropertyName("start_url")]
    public string StartUrl { get; set; } = "";
}

/// <summary>One row of ``GET /portal/connectors`` — connection state only,
/// never token material.</summary>
public sealed class ConnectorConnection
{
    [JsonPropertyName("connector_id")]
    public string ConnectorId { get; set; } = "";

    [JsonPropertyName("provider")]
    public string Provider { get; set; } = "";

    [JsonPropertyName("connected")]
    public bool Connected { get; set; }

    [JsonPropertyName("expires_at")]
    public DateTimeOffset? ExpiresAt { get; set; }

    [JsonPropertyName("scopes")]
    public string Scopes { get; set; } = "";
}
