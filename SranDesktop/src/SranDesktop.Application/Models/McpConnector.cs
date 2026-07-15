using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>A remote MCP connector catalog entry (R9 Feature I). Phase 12 D
/// adds local-process (stdio) connectors: transport "stdio" + command/args/env
/// instead of a server_url.</summary>
public sealed class McpConnector
{
    /// <summary>Remote MCP server over streamable HTTP (the default —
    /// every pre-Phase-12 connector deserializes to this).</summary>
    public const string TransportHttp = "http";

    /// <summary>Local child process speaking newline-delimited JSON-RPC
    /// over stdin/stdout (the MCP stdio transport).</summary>
    public const string TransportStdio = "stdio";

    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("description")]
    public string Description { get; set; } = "";

    /// <summary>communication | productivity | developer | data</summary>
    [JsonPropertyName("category")]
    public string Category { get; set; } = "productivity";

    /// <summary>none | api_key | oauth2</summary>
    [JsonPropertyName("auth_type")]
    public string AuthType { get; set; } = "none";

    /// <summary>Remote MCP server base URL; tool calls proxy to {url}/execute.</summary>
    [JsonPropertyName("server_url")]
    public string ServerUrl { get; set; } = "";

    /// <summary>Tool names this connector contributes.</summary>
    [JsonPropertyName("tools")]
    public List<string> Tools { get; set; } = new();

    /// <summary>"http" | "stdio" (Phase 12 D). Absent in legacy JSON →
    /// defaults to http, so existing connectors keep behaving identically.</summary>
    [JsonPropertyName("transport")]
    public string Transport { get; set; } = TransportHttp;

    /// <summary>stdio only: executable to spawn (absolute path or on PATH).</summary>
    [JsonPropertyName("command")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Command { get; set; }

    /// <summary>stdio only: arguments for <see cref="Command"/>.</summary>
    [JsonPropertyName("args")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<string>? Args { get; set; }

    /// <summary>stdio only: extra environment variables for the process.</summary>
    [JsonPropertyName("env")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public Dictionary<string, string>? Env { get; set; }
}

/// <summary>Locally persisted install record for a connector.</summary>
public sealed class InstalledConnector
{
    /// <summary>Broker-mode oauth2 install (Phase 7): NO local secret —
    /// tool calls authenticate with the live portal JWT and the provider
    /// token stays on the backend.</summary>
    public const string AuthModeOAuth2 = "oauth2";

    /// <summary>Pasted-key install: the token lives in the DPAPI store
    /// under ``connector:{id}``.</summary>
    public const string AuthModeApiKey = "api_key";

    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("installed_at")]
    public DateTimeOffset InstalledAt { get; set; } = DateTimeOffset.UtcNow;

    /// <summary>Set when the connector's auth completed (token in DPAPI store).
    /// Always false for oauth2 installs — that's the point.</summary>
    [JsonPropertyName("has_credentials")]
    public bool HasCredentials { get; set; }

    /// <summary>"oauth2" | "api_key" | null (legacy install — treated as
    /// api_key/DPAPI so pre-Phase-7 records keep working).</summary>
    [JsonPropertyName("auth_mode")]
    public string? AuthMode { get; set; }

    /// <summary>Phase 12 D: full definition of a locally-added connector
    /// (typically transport "stdio") that exists in no catalog. Null for
    /// catalog installs — and omitted when writing connectors.json so
    /// pre-existing records re-serialize byte-identically.</summary>
    [JsonPropertyName("custom")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public McpConnector? Custom { get; set; }
}
