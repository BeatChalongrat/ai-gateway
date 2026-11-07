using System.Text.Json;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>Health of an installed connector's MCP server (R10 1E).</summary>
public enum ConnectorStatus
{
    /// <summary>Not installed, or never reached the server.</summary>
    NotConfigured,
    Connected,
    Disconnected
}

/// <summary>Remote MCP connector catalog + install state + live protocol client
/// (R9 Feature I, upgraded to a real JSON-RPC client in R10 Fix 1).</summary>
public interface IMcpConnectorService
{
    /// <summary>Catalog of connectors: remote catalog URL when configured,
    /// embedded fallback otherwise.</summary>
    Task<IReadOnlyList<McpConnector>> ListAvailableAsync(CancellationToken ct = default);

    /// <summary>FIX69: true when the catalogue in use is the embedded starter one,
    /// whose server_url values are placeholders (no such hosts exist). The page
    /// hides key-based catalogue cards in that state — a button with no
    /// destination must not be shown.</summary>
    bool CatalogIsEmbedded { get; }

    Task<IReadOnlyList<InstalledConnector>> GetInstalledAsync(CancellationToken ct = default);

    /// <summary>Install: persist config; optionally store an API token (DPAPI).
    /// ``authMode`` = <see cref="InstalledConnector.AuthModeOAuth2"/> records a
    /// broker-mode install that carries NO local secret (Phase 7 C3) — its
    /// tool calls authenticate with the live portal JWT instead.</summary>
    Task InstallAsync(string connectorId, string? apiToken = null, string? authMode = null, CancellationToken ct = default);

    /// <summary>Phase 12 D: install a locally-defined connector that exists in
    /// no catalog — typically a stdio MCP server (transport "stdio" +
    /// command/args/env). The full definition is persisted in connectors.json.
    /// Its tools flow through the SAME remote-tool consent path as catalog
    /// connectors (RemoteMcpTool → PermissionLevel.Ask): nothing a local
    /// process exposes can auto-run. Custom-connector UI: throws
    /// ArgumentException on an invalid definition or duplicate id;
    /// <paramref name="apiToken"/> goes to the DPAPI store, never to
    /// connectors.json.</summary>
    Task InstallCustomAsync(McpConnector connector, string? apiToken = null, CancellationToken ct = default);

    /// <summary>Custom-connector UI: dial a NOT-yet-installed definition
    /// (initialize + tools/list) and report the tool count or error inline.
    /// Persists nothing; a stdio test spawns — and then kills — the process.</summary>
    Task<ConnectorTestResult> TestConnectionAsync(McpConnector connector, string? apiToken = null, CancellationToken ct = default);

    /// <summary>Cached tool names of ONE connected connector (empty when not
    /// connected) — the installed-list card shows live tool names/count.</summary>
    IReadOnlyList<string> GetCachedToolNames(string connectorId);

    Task UninstallAsync(string connectorId, CancellationToken ct = default);

    bool IsInstalled(string connectorId);

    // ── Live MCP protocol (R10) ─────────────────────────────────────────────

    /// <summary>Connect to every installed connector and cache its tools/list.
    /// Failures mark the connector Disconnected; never throws.</summary>
    Task RefreshRemoteToolsAsync(CancellationToken ct = default);

    /// <summary>Cached tool definitions from connected servers (sync — safe to
    /// call from the per-turn tool assembly path).</summary>
    IReadOnlyList<GatewayToolDefinition> GetCachedRemoteToolDefinitions();

    /// <summary>True (with owner) when a tool name belongs to a connected server.</summary>
    bool TryResolveRemoteTool(string toolName, out string connectorId);

    /// <summary>Execute a remote tool over MCP.</summary>
    Task<ToolResult> ExecuteRemoteToolAsync(string toolName, JsonElement arguments, CancellationToken ct = default);

    /// <summary>tools/list round-trip as a health probe; updates status.</summary>
    Task<bool> PingAsync(string connectorId, CancellationToken ct = default);

    ConnectorStatus GetStatus(string connectorId);

    /// <summary>Start the 5-minute background health check (idempotent).</summary>
    void StartHealthChecks();

    /// <summary>Raised whenever any connector's status changes.</summary>
    event EventHandler? StatusChanged;
}
