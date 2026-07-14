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

    Task<IReadOnlyList<InstalledConnector>> GetInstalledAsync(CancellationToken ct = default);

    /// <summary>Install: persist config; optionally store an API token (DPAPI).
    /// ``authMode`` = <see cref="InstalledConnector.AuthModeOAuth2"/> records a
    /// broker-mode install that carries NO local secret (Phase 7 C3) — its
    /// tool calls authenticate with the live portal JWT instead.</summary>
    Task InstallAsync(string connectorId, string? apiToken = null, string? authMode = null, CancellationToken ct = default);

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
