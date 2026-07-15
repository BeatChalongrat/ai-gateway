using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Remote;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Connector marketplace backend (R9 Feature I) + real MCP protocol client
/// (R10 Fix 1). Catalog: remote URL (Sran:McpCatalogUrl) with embedded
/// fallback. Install state: %APPDATA%\SRAN Desktop\connectors.json; tokens in
/// DPAPI. Installed connectors are dialed with <see cref="McpRemoteClient"/>;
/// their tools/list is cached so the per-turn tool assembly stays synchronous,
/// and a 5-minute health check keeps status dots honest.
/// </summary>
public sealed class McpConnectorService : IMcpConnectorService, IAsyncDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    private readonly string _stateFile;
    private readonly ICredentialStore _credentials;
    private readonly ILogger<McpConnectorService> _logger;
    private readonly string? _catalogUrl;
    // Phase 7 C4: source of the live portal JWT for oauth2 (broker-mode)
    // connectors. Nullable so existing tests/fixtures keep constructing
    // the service without it (oauth2 dials then send no Bearer and fail
    // like a signed-out session would).
    private readonly ITokenSession? _tokenSession;
    private readonly HttpClient _catalogHttp = new() { Timeout = TimeSpan.FromSeconds(15) };

    private List<InstalledConnector>? _installed;
    private List<McpConnector>? _catalog;

    private readonly object _remoteLock = new();
    private readonly Dictionary<string, McpRemoteClient> _clients = new();
    private readonly Dictionary<string, List<McpToolDefinition>> _remoteTools = new();
    private readonly Dictionary<string, string> _toolOwner = new(); // tool name → connector id
    private readonly Dictionary<string, ConnectorStatus> _status = new();
    private Timer? _healthTimer;

    public event EventHandler? StatusChanged;

    // Bounds ONE connector's whole dial (initialize + notification +
    // tools/list — each HTTP call has its own 30s client timeout, so a hung
    // server could otherwise hold a dial ~90s). Ctor-overridable for tests.
    private readonly TimeSpan _dialTimeout;

    public McpConnectorService(
        ICredentialStore credentials, ILogger<McpConnectorService> logger,
        string? catalogUrl = null, ITokenSession? tokenSession = null,
        string? stateFilePath = null, TimeSpan? dialTimeout = null)
    {
        _credentials = credentials;
        _logger = logger;
        _catalogUrl = catalogUrl;
        _tokenSession = tokenSession;
        _dialTimeout = dialTimeout ?? TimeSpan.FromSeconds(45);
        // stateFilePath is a test seam — production always uses %APPDATA%.
        _stateFile = stateFilePath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "SRAN Desktop", "connectors.json");
    }

    // ── Catalog (R10 1D: remote with embedded fallback) ─────────────────────

    public async Task<IReadOnlyList<McpConnector>> ListAvailableAsync(CancellationToken ct = default)
    {
        if (_catalog is not null)
            return _catalog;

        if (!string.IsNullOrEmpty(_catalogUrl))
        {
            try
            {
                var json = await _catalogHttp.GetStringAsync(_catalogUrl, ct);
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement.TryGetProperty("connectors", out var list)
                    ? list : doc.RootElement;
                var remote = JsonSerializer.Deserialize<List<McpConnector>>(
                    root.GetRawText(), new JsonSerializerOptions(JsonSerializerDefaults.Web));
                if (remote is { Count: > 0 })
                    return _catalog = remote;
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
            {
                _logger.LogWarning(ex, "Remote connector catalog unavailable — using embedded");
            }
        }
        return _catalog = EmbeddedCatalog;
    }

    public Task<IReadOnlyList<InstalledConnector>> GetInstalledAsync(CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<InstalledConnector>>(LoadState());

    public bool IsInstalled(string connectorId) =>
        LoadState().Any(c => c.Id == connectorId);

    public async Task InstallAsync(string connectorId, string? apiToken = null, string? authMode = null, CancellationToken ct = default)
    {
        var state = LoadState();
        var record = state.FirstOrDefault(c => c.Id == connectorId);
        if (record is null)
        {
            record = new InstalledConnector { Id = connectorId };
            state.Add(record);
        }
        record.HasCredentials |= apiToken is not null;
        if (authMode is not null)
            record.AuthMode = authMode;
        else if (apiToken is not null)
            record.AuthMode = InstalledConnector.AuthModeApiKey;

        // oauth2 (broker-mode) installs carry NO local secret — the DPAPI
        // entry is neither written nor read for them (Phase 7 C3/C4).
        if (apiToken is not null && record.AuthMode != InstalledConnector.AuthModeOAuth2)
            _credentials.Save($"connector:{connectorId}", apiToken);

        SaveState(state);
        _logger.LogInformation("Connector {Id} installed", connectorId);

        // Dial the server right away so its tools go live this session.
        await ConnectOneAsync(connectorId, ct);
    }

    /// <summary>Phase 12 D: locally-defined connector (typically stdio). The
    /// whole definition rides in connectors.json (record.Custom); catalog
    /// lookups are bypassed for it. Its tools land in the same remote-tool
    /// cache, so they resolve as RemoteMcpTool → consent-gated Ask.</summary>
    public async Task InstallCustomAsync(McpConnector connector, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(connector.Id))
            throw new ArgumentException("Custom connector requires an id", nameof(connector));

        var state = LoadState();
        var record = state.FirstOrDefault(c => c.Id == connector.Id);
        if (record is null)
        {
            record = new InstalledConnector { Id = connector.Id };
            state.Add(record);
        }
        record.Custom = connector;
        SaveState(state);
        _logger.LogInformation("Custom connector {Id} installed (transport={Transport})",
            connector.Id, connector.Transport);

        await ConnectOneAsync(connector.Id, ct);
    }

    public async Task UninstallAsync(string connectorId, CancellationToken ct = default)
    {
        var state = LoadState();
        state.RemoveAll(c => c.Id == connectorId);
        SaveState(state);
        try
        {
            _credentials.Delete($"connector:{connectorId}");
        }
        catch (IOException ex)
        {
            _logger.LogDebug(ex, "No stored credential for {Id}", connectorId);
        }

        McpRemoteClient? client = null;
        lock (_remoteLock)
        {
            if (_clients.Remove(connectorId, out client)) { }
            _remoteTools.Remove(connectorId);
            RebuildToolOwners();
            _status.Remove(connectorId);
        }
        if (client is not null)
            await client.DisposeAsync();
        StatusChanged?.Invoke(this, EventArgs.Empty);
    }

    // ── Live protocol (R10 1B/1C/1E) ────────────────────────────────────────

    public async Task RefreshRemoteToolsAsync(CancellationToken ct = default)
    {
        // Phase 10 D4: dial connectors in PARALLEL with a bounded per-dial
        // timeout — one hung server must not serialize the health sweep or
        // stall the post-login initial dial. Racing dials are already safe:
        // GetOrCreateClientAsync keeps the first client, and every shared map
        // mutation happens under _remoteLock.
        var installed = LoadState().ToList();
        if (installed.Count == 0)
            return;
        await Task.WhenAll(installed.Select(record => DialOneBoundedAsync(record.Id, ct)));
    }

    private async Task DialOneBoundedAsync(string connectorId, CancellationToken ct)
    {
        using var dialCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        dialCts.CancelAfter(_dialTimeout);
        try
        {
            await ConnectOneAsync(connectorId, dialCts.Token);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            // Safety net: most timeouts surface as TaskCanceledException and
            // are already handled inside ConnectOneAsync; a bare OCE that
            // escapes still gets the same status treatment.
            _logger.LogWarning("Connector {Id} dial timed out after {Timeout}",
                connectorId, _dialTimeout);
            await DropClientAsync(connectorId);
            SetStatus(connectorId, ConnectorStatus.Disconnected);
        }
    }

    private async Task ConnectOneAsync(string connectorId, CancellationToken ct)
    {
        var connector = await FindConnectorAsync(connectorId, ct);
        if (connector is null || !HasEndpoint(connector))
        {
            SetStatus(connectorId, ConnectorStatus.NotConfigured);
            return;
        }

        try
        {
            var client = await GetOrCreateClientAsync(connector, ct);
            var tools = await client.ListToolsAsync(ct);
            lock (_remoteLock)
            {
                _remoteTools[connectorId] = tools;
                RebuildToolOwners();
            }
            SetStatus(connectorId, ConnectorStatus.Connected);
            _logger.LogInformation("Connector {Id}: {Count} remote tools", connectorId, tools.Count);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                       or McpProtocolException or JsonException or IOException)
        {
            _logger.LogWarning(ex, "Connector {Id} unreachable", connectorId);
            await DropClientAsync(connectorId);
            SetStatus(connectorId, FailureStatus(connectorId, ex));
        }
    }

    private async Task<McpRemoteClient> GetOrCreateClientAsync(McpConnector connector, CancellationToken ct)
    {
        lock (_remoteLock)
        {
            if (_clients.TryGetValue(connector.Id, out var existing))
                return existing;
        }

        var client = new McpRemoteClient(CreateTransport(connector));
        await client.InitializeAsync(ct);
        lock (_remoteLock)
        {
            // A racing dial may have won; keep the first.
            if (_clients.TryGetValue(connector.Id, out var raced))
            {
                // Dispose the loser — for stdio that kills its orphan process.
                _ = client.DisposeAsync();
                return raced;
            }
            _clients[connector.Id] = client;
        }
        return client;
    }

    /// <summary>Phase 12 D transport selection: "stdio" spawns a local server
    /// process; everything else (including every legacy connector, which has
    /// no transport field) keeps the streamable-HTTP path unchanged.
    /// Internal for tests.</summary>
    internal IMcpTransport CreateTransport(McpConnector connector)
    {
        if (connector.Transport == McpConnector.TransportStdio)
        {
            var transport = new StdioMcpTransport(
                connector.Command ?? "", connector.Args, connector.Env, _logger);
            var connectorId = connector.Id;
            // Process crash/exit surfaces as Disconnected through the SAME
            // StatusChanged path an HTTP connector going dark uses.
            transport.Exited += (_, _) => _ = HandleStdioExitAsync(connectorId);
            return transport;
        }
        return new HttpMcpTransport(connector.ServerUrl, ResolveTokenProvider(connector));
    }

    private async Task HandleStdioExitAsync(string connectorId)
    {
        _logger.LogWarning("Connector {Id}: local MCP server process exited", connectorId);
        await DropClientAsync(connectorId);
        SetStatus(connectorId, ConnectorStatus.Disconnected);
    }

    /// <summary>Custom (locally-defined) connectors win over the catalog.</summary>
    private async Task<McpConnector?> FindConnectorAsync(string connectorId, CancellationToken ct)
    {
        var custom = LoadState().FirstOrDefault(c => c.Id == connectorId)?.Custom;
        if (custom is not null)
            return custom;
        return (await ListAvailableAsync(ct)).FirstOrDefault(c => c.Id == connectorId);
    }

    private static bool HasEndpoint(McpConnector connector) =>
        connector.Transport == McpConnector.TransportStdio
            ? !string.IsNullOrEmpty(connector.Command)
            : !string.IsNullOrEmpty(connector.ServerUrl);

    /// <summary>Phase 10 C1: built-in (auth_type "none") gateway servers now
    /// REQUIRE the portal JWT — the mcp-gateway rejects anonymous calls and
    /// scopes the filesystem/memory workspace per user. They carry no local
    /// credential, so send the live portal access token (resolved per request,
    /// like oauth2). Everything else keeps its Phase 7 source below. Only
    /// "none" gets this treatment on purpose: an api_key connector with a
    /// missing DPAPI entry must keep sending NOTHING rather than leak the
    /// portal JWT to a third-party server URL.</summary>
    internal Func<string?> ResolveTokenProvider(McpConnector connector) =>
        connector.AuthType == "none"
            ? () => _tokenSession?.AccessToken
            : ResolveTokenProvider(connector.Id);

    /// <summary>Phase 7 C4: pick the Bearer source by install mode. oauth2
    /// (broker) connectors send the CURRENT portal access token, resolved
    /// per request so JWT refreshes are picked up; api_key / legacy installs
    /// keep the DPAPI token. Internal for tests.</summary>
    internal Func<string?> ResolveTokenProvider(string connectorId)
    {
        var installed = LoadState().FirstOrDefault(c => c.Id == connectorId);
        if (installed?.AuthMode == InstalledConnector.AuthModeOAuth2)
            return () => _tokenSession?.AccessToken;

        var token = _credentials.Load($"connector:{connectorId}");
        return () => token;
    }

    /// <summary>An unauthorized oauth2 connector needs a RE-CONNECT, not a
    /// retry — surface it as NotConfigured instead of Disconnected.</summary>
    private ConnectorStatus FailureStatus(string connectorId, Exception ex)
    {
        if (ex is HttpRequestException { StatusCode: System.Net.HttpStatusCode.Unauthorized }
            && LoadState().FirstOrDefault(c => c.Id == connectorId)?.AuthMode
                == InstalledConnector.AuthModeOAuth2)
            return ConnectorStatus.NotConfigured;
        return ConnectorStatus.Disconnected;
    }

    private async Task DropClientAsync(string connectorId)
    {
        McpRemoteClient? client;
        lock (_remoteLock)
        {
            _clients.Remove(connectorId, out client);
            _remoteTools.Remove(connectorId);
            RebuildToolOwners();
        }
        if (client is not null)
            await client.DisposeAsync();
    }

    public IReadOnlyList<GatewayToolDefinition> GetCachedRemoteToolDefinitions()
    {
        lock (_remoteLock)
        {
            var defs = new List<GatewayToolDefinition>();
            foreach (var (_, tools) in _remoteTools)
            {
                foreach (var tool in tools)
                {
                    JsonElement schema;
                    try
                    {
                        using var doc = JsonDocument.Parse(tool.InputSchema);
                        schema = doc.RootElement.Clone();
                    }
                    catch (JsonException)
                    {
                        using var doc = JsonDocument.Parse("{\"type\":\"object\"}");
                        schema = doc.RootElement.Clone();
                    }
                    defs.Add(new GatewayToolDefinition
                    {
                        Function = new GatewayFunctionDef
                        {
                            Name = tool.Name,
                            Description = tool.Description ?? $"Remote tool from {_toolOwner.GetValueOrDefault(tool.Name)}",
                            Parameters = schema
                        }
                    });
                }
            }
            return defs;
        }
    }

    public bool TryResolveRemoteTool(string toolName, out string connectorId)
    {
        lock (_remoteLock)
        {
            return _toolOwner.TryGetValue(toolName, out connectorId!);
        }
    }

    public async Task<ToolResult> ExecuteRemoteToolAsync(
        string toolName, JsonElement arguments, CancellationToken ct = default)
    {
        if (!TryResolveRemoteTool(toolName, out var connectorId))
            return ToolResult.Fail($"Unknown remote tool '{toolName}'.");

        var connector = await FindConnectorAsync(connectorId, ct);
        if (connector is null)
            return ToolResult.Fail($"Connector '{connectorId}' is no longer available.");

        try
        {
            var client = await GetOrCreateClientAsync(connector, ct);
            var result = await client.CallToolAsync(toolName, arguments, ct);
            SetStatus(connectorId, ConnectorStatus.Connected);
            return result.IsError ? ToolResult.Fail(result.Content) : ToolResult.Ok(result.Content);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                       or McpProtocolException or JsonException or IOException)
        {
            _logger.LogWarning(ex, "Remote tool {Tool} via {Id} failed", toolName, connectorId);
            await DropClientAsync(connectorId);
            SetStatus(connectorId, FailureStatus(connectorId, ex));
            return ToolResult.Fail(
                $"The {connector.Name} connector is unreachable: {ex.Message}");
        }
    }

    public async Task<bool> PingAsync(string connectorId, CancellationToken ct = default)
    {
        var connector = await FindConnectorAsync(connectorId, ct);
        if (connector is null || !HasEndpoint(connector))
        {
            SetStatus(connectorId, ConnectorStatus.NotConfigured);
            return false;
        }
        try
        {
            var client = await GetOrCreateClientAsync(connector, ct);
            await client.ListToolsAsync(ct);
            SetStatus(connectorId, ConnectorStatus.Connected);
            return true;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                       or McpProtocolException or JsonException or IOException)
        {
            await DropClientAsync(connectorId);
            SetStatus(connectorId, FailureStatus(connectorId, ex));
            return false;
        }
    }

    public ConnectorStatus GetStatus(string connectorId)
    {
        lock (_remoteLock)
        {
            return _status.GetValueOrDefault(connectorId, ConnectorStatus.NotConfigured);
        }
    }

    public void StartHealthChecks()
    {
        _healthTimer ??= new Timer(
            _ => _ = RefreshRemoteToolsAsync(),
            null, TimeSpan.FromSeconds(20), TimeSpan.FromMinutes(5));
    }

    private void SetStatus(string connectorId, ConnectorStatus status)
    {
        bool changed;
        lock (_remoteLock)
        {
            changed = _status.GetValueOrDefault(connectorId, ConnectorStatus.NotConfigured) != status;
            _status[connectorId] = status;
        }
        if (changed)
            StatusChanged?.Invoke(this, EventArgs.Empty);
    }

    private void RebuildToolOwners()
    {
        _toolOwner.Clear();
        foreach (var (connectorId, tools) in _remoteTools)
            foreach (var tool in tools)
                _toolOwner[tool.Name] = connectorId; // last writer wins on collision
    }

    // ── Install state ───────────────────────────────────────────────────────

    private List<InstalledConnector> LoadState()
    {
        if (_installed is not null)
            return _installed;
        try
        {
            if (File.Exists(_stateFile))
                return _installed = JsonSerializer.Deserialize<List<InstalledConnector>>(
                    File.ReadAllText(_stateFile)) ?? new();
        }
        catch (Exception ex) when (ex is JsonException or IOException)
        {
            _logger.LogWarning(ex, "connectors.json unreadable");
        }
        return _installed = new List<InstalledConnector>();
    }

    private void SaveState(List<InstalledConnector> state)
    {
        _installed = state;
        Directory.CreateDirectory(Path.GetDirectoryName(_stateFile)!);
        Storage.AtomicFile.Write(_stateFile, JsonSerializer.Serialize(state, JsonOptions));
    }

    public async ValueTask DisposeAsync()
    {
        _healthTimer?.Dispose();
        List<McpRemoteClient> clients;
        lock (_remoteLock)
        {
            clients = _clients.Values.ToList();
            _clients.Clear();
        }
        foreach (var client in clients)
            await client.DisposeAsync();
        _catalogHttp.Dispose();
    }

    /// <summary>Embedded starter catalog. server_url values are placeholders —
    /// point Sran:McpCatalogUrl at a real catalog at deploy time.</summary>
    private static readonly List<McpConnector> EmbeddedCatalog = new()
    {
        new() { Id = "slack", Name = "Slack", Category = "communication", AuthType = "oauth2",
                Description = "Send messages, read channels, search your Slack workspace",
                ServerUrl = "https://mcp.sran.ai/slack",
                Tools = new() { "slack_send_message", "slack_read_channel", "slack_search" } },
        new() { Id = "gmail", Name = "Gmail", Category = "communication", AuthType = "oauth2",
                Description = "Read, search and draft emails in your Gmail account",
                ServerUrl = "https://mcp.sran.ai/gmail",
                Tools = new() { "gmail_search", "gmail_read", "gmail_draft" } },
        new() { Id = "gdrive", Name = "Google Drive", Category = "productivity", AuthType = "oauth2",
                Description = "Search and read documents in your Google Drive",
                ServerUrl = "https://mcp.sran.ai/gdrive",
                Tools = new() { "gdrive_search", "gdrive_read" } },
        new() { Id = "notion", Name = "Notion", Category = "productivity", AuthType = "api_key",
                Description = "Search and read pages in your Notion workspace",
                ServerUrl = "https://mcp.sran.ai/notion",
                Tools = new() { "notion_search", "notion_read_page", "notion_create_page" } },
        new() { Id = "github", Name = "GitHub", Category = "developer", AuthType = "api_key",
                Description = "Search repos, read issues and pull requests",
                ServerUrl = "https://mcp.sran.ai/github",
                Tools = new() { "github_search", "github_read_issue", "github_list_prs" } },
        new() { Id = "jira", Name = "Jira", Category = "developer", AuthType = "api_key",
                Description = "Search and update issues in your Jira projects",
                ServerUrl = "https://mcp.sran.ai/jira",
                Tools = new() { "jira_search", "jira_read_issue", "jira_update_issue" } },
        new() { Id = "gcal", Name = "Google Calendar", Category = "productivity", AuthType = "oauth2",
                Description = "Read and create events in your Google Calendar",
                ServerUrl = "https://mcp.sran.ai/gcal",
                Tools = new() { "gcal_list_events", "gcal_create_event" } },
        new() { Id = "postgres", Name = "PostgreSQL", Category = "data", AuthType = "api_key",
                Description = "Run read-only queries against a PostgreSQL database",
                ServerUrl = "https://mcp.sran.ai/postgres",
                Tools = new() { "postgres_query", "postgres_schema" } },
    };
}
