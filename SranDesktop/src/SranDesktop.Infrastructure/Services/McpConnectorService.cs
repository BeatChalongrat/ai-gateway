using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Connectors;
using SranDesktop.Infrastructure.Mcp.Remote;

namespace SranDesktop.Infrastructure.Services;

/// <summary>
/// Connector marketplace backend (R9 Feature I) + install state.
///
/// Catalog: remote URL (Sran:McpCatalogUrl) with embedded fallback. Install
/// state: %APPDATA%\SRAN Desktop\connectors.json; tokens in DPAPI.
///
/// Phase 4.1C removed the protocol client from this class entirely. It used to
/// dial servers itself, which meant a marketplace connector's tool call reached
/// the outside world with no AgentStep, no trust check, no recorded dispatch
/// intent, and no reconciliation when its answer was lost. Now every protocol
/// operation — the dial, the tools/list, the health probe, the tool call, the
/// pre-install test — goes through <see cref="IMcpDurableGateway"/>, and what is
/// left here is what this class was always about: the catalog, the install
/// records, the credentials, and the cached tool list the per-turn assembly
/// reads synchronously.
/// </summary>
public sealed class McpConnectorService
    : IMcpConnectorService, IConnectorEndpointAuthority, IAsyncDisposable
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
    private readonly Dictionary<string, List<McpToolDefinition>> _remoteTools = new();
    private readonly Dictionary<string, string> _toolOwner = new(); // tool name → connector id
    private readonly Dictionary<string, ConnectorStatus> _status = new();
    private Timer? _healthTimer;

    // Resolved lazily: the gateway's session provider needs this service for
    // endpoint credentials, so a constructor dependency would be a cycle.
    private readonly Func<IMcpDurableGateway?>? _gateway;
    private readonly Func<IMcpConnectorSessionProvider?>? _sessions;
    private readonly TransientConnectorGrants? _candidates;
    private readonly IConnectorTrustAdministration? _trustAdministration;
    private readonly IConnectorTrustStore? _trustStore;
    private readonly ICredentialBroker? _broker;

    public event EventHandler? StatusChanged;

    // Bounds ONE connector's whole dial (initialize + notification +
    // tools/list — each HTTP call has its own 30s client timeout, so a hung
    // server could otherwise hold a dial ~90s). Ctor-overridable for tests.
    private readonly TimeSpan _dialTimeout;

    public McpConnectorService(
        ICredentialStore credentials, ILogger<McpConnectorService> logger,
        string? catalogUrl = null, ITokenSession? tokenSession = null,
        string? stateFilePath = null, TimeSpan? dialTimeout = null,
        Func<IMcpDurableGateway?>? gateway = null,
        TransientConnectorGrants? candidates = null,
        IConnectorTrustAdministration? trustAdministration = null,
        IConnectorTrustStore? trustStore = null,
        Func<IMcpConnectorSessionProvider?>? sessions = null,
        ICredentialBroker? broker = null)
    {
        _sessions = sessions;
        _broker = broker;
        _credentials = credentials;
        _logger = logger;
        _catalogUrl = catalogUrl;
        _tokenSession = tokenSession;
        _dialTimeout = dialTimeout ?? TimeSpan.FromSeconds(45);
        _gateway = gateway;
        _candidates = candidates;
        _trustAdministration = trustAdministration;
        _trustStore = trustStore;
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

    public async Task InstallAsync(
        string connectorId, string? apiToken = null, string? authMode = null,
        UserApprovalEvidence? evidence = null, CancellationToken ct = default)
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

        await ApproveInstallAsync(connectorId, evidence, ct);

        // Dial the server right away so its tools go live this session.
        await ConnectOneAsync(connectorId, ct);
    }

    /// <summary>
    /// Records the approval that the install click IS.
    ///
    /// For a remote connector, choosing it in the marketplace and — for an
    /// api_key server — pasting a credential for it is the decision: the user
    /// picked this connector and this endpoint. What was missing before was
    /// writing that decision down, which is why the endpoint could later change
    /// underneath it. Without evidence of a person the install still happens but
    /// the connector lands unapproved, so it shows up needing a decision rather
    /// than quietly working.
    /// </summary>
    private async Task ApproveInstallAsync(
        string connectorId, UserApprovalEvidence? evidence, CancellationToken ct)
    {
        if (_trustAdministration is null)
            return;
        var connector = await FindConnectorAsync(connectorId, ct);
        if (connector is null || connector.Transport == McpConnector.TransportStdio)
            return;
        if (evidence is null)
        {
            _logger.LogWarning(
                "Connector {Id} was installed without a recorded user decision; it must be "
                + "approved before it can run", connectorId);
            return;
        }
        try
        {
            await _trustAdministration.ApproveAsync(
                connectorId,
                new ConnectorApprovalDecision(
                    ConnectorApprovalOutcome.Approved, evidence.Principal,
                    "Approved as part of installing the connector."),
                evidence,
                ct);
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException
                                       or UnauthorizedAccessException)
        {
            _logger.LogError(ex, "Connector {Id} trust could not be recorded", connectorId);
        }
    }

    /// <summary>Phase 12 D: locally-defined connector (typically stdio). The
    /// whole definition rides in connectors.json (record.Custom); catalog
    /// lookups are bypassed for it. Its tools land in the same remote-tool
    /// cache, so they resolve as RemoteMcpTool → consent-gated Ask. The
    /// custom-connector UI adds validation (fail loud on a bad definition —
    /// a silent bad install would just sit Disconnected forever) and an
    /// api_key path whose token goes to DPAPI, never connectors.json.</summary>
    public async Task InstallCustomAsync(
        McpConnector connector, string? apiToken = null,
        UserApprovalEvidence? evidence = null, CancellationToken ct = default)
    {
        ValidateCustomDefinition(connector);
        if (connector.Transport == McpConnector.TransportStdio)
            // A local server is a binary this machine will execute. It belongs in
            // the managed connector folder with a manifest that says what it wants,
            // which is what the approval screen reads — a command line pasted into
            // a text box carries none of that.
            throw new ArgumentException(
                "A local (stdio) MCP server cannot be added this way. Install it into the "
                + "managed connector folder with a manifest, then approve it.",
                nameof(connector));

        var state = LoadState();
        if (state.Any(c => c.Id == connector.Id))
            throw new ArgumentException(
                $"A connector with id '{connector.Id}' is already installed", nameof(connector));

        var record = new InstalledConnector { Id = connector.Id, Custom = connector };
        if (!string.IsNullOrEmpty(apiToken))
        {
            record.HasCredentials = true;
            record.AuthMode = InstalledConnector.AuthModeApiKey;
            _credentials.Save($"connector:{connector.Id}", apiToken);
        }
        state.Add(record);
        SaveState(state);
        _logger.LogInformation("Custom connector {Id} installed (transport={Transport})",
            connector.Id, connector.Transport);

        await ApproveInstallAsync(connector.Id, evidence, ct);
        await ConnectOneAsync(connector.Id, ct);
    }

    /// <summary>Rejects definitions that could only ever sit Disconnected:
    /// missing id/name, stdio without a command, HTTP without a valid URL.
    /// https is REQUIRED for non-loopback hosts — an API key must never
    /// travel over cleartext to a remote server. Internal for tests.</summary>
    internal static void ValidateCustomDefinition(McpConnector connector)
    {
        if (string.IsNullOrWhiteSpace(connector.Id))
            throw new ArgumentException("Custom connector requires an id", nameof(connector));
        if (string.IsNullOrWhiteSpace(connector.Name))
            throw new ArgumentException("Custom connector requires a name", nameof(connector));

        if (connector.Transport == McpConnector.TransportStdio)
        {
            if (string.IsNullOrWhiteSpace(connector.Command))
                throw new ArgumentException("stdio connector requires a command", nameof(connector));
            if (connector.Env is { Count: > 0 })
                throw new ArgumentException(
                    "stdio connector environment values are not accepted; use an explicitly scoped credential flow",
                    nameof(connector));
            if (connector.Args?.Any(argument =>
                    Application.Services.SensitiveDataRedactor.Classify(argument)
                    != Domain.Entities.SensitiveDataClassification.Internal) == true)
                throw new ArgumentException(
                    "stdio connector arguments contain sensitive data; use an explicitly scoped credential flow",
                    nameof(connector));
            return;
        }

        if (!Uri.TryCreate(connector.ServerUrl, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https"))
            throw new ArgumentException(
                "HTTP connector requires an absolute http(s) server URL", nameof(connector));
        if (uri.Scheme == "http" && !uri.IsLoopback)
            throw new ArgumentException(
                "https is required for non-localhost servers", nameof(connector));
    }

    /// <summary>
    /// Custom-connector UI "Test connection": dial a definition that is not
    /// installed yet and report the tool count.
    ///
    /// The dial goes through the durable gateway like every other MCP operation,
    /// which means it leaves an AgentStep behind and its result arrives as an
    /// untrusted observation. Reaching a not-yet-installed connector needs a
    /// temporary grant, and that grant needs evidence a person asked for it —
    /// without <paramref name="evidence"/> the test is refused rather than run
    /// on the agent's say-so.
    ///
    /// A stdio definition is refused outright: launching an unapproved local
    /// executable is the exact risk approval exists to gate, and "just to test
    /// it" runs the same code as running it.
    /// </summary>
    public async Task<ConnectorTestResult> TestConnectionAsync(
        McpConnector connector,
        string? apiToken = null,
        UserApprovalEvidence? evidence = null,
        CancellationToken ct = default)
    {
        try
        {
            ValidateCustomDefinition(connector);
        }
        catch (ArgumentException ex)
        {
            return ConnectorTestResult.Fail(ex.Message);
        }

        if (connector.Transport == McpConnector.TransportStdio)
            return ConnectorTestResult.Fail(
                "A local (stdio) connector cannot be dialled before it is installed into "
                + "the managed connector folder and approved. Install it, review what it "
                + "asks for, then approve it.");

        // The policy check comes before the availability check: "who asked for
        // this" is not a question whose answer depends on whether the runtime
        // happens to be composed.
        if (evidence is null)
            return ConnectorTestResult.Fail(
                "Testing a connection reaches an external server, so it has to be started "
                + "by a person.");
        if (_gateway?.Invoke() is not { } gateway || _candidates is null)
            return ConnectorTestResult.Fail(
                "The connector runtime is unavailable, so the connection cannot be tested.");

        // The pasted token is used directly — it isn't in DPAPI yet, and the
        // regular provider must not run: an auth-none custom definition would
        // resolve the portal JWT (see ResolveTokenProvider's custom guard).
        var previousOverride = _tokenOverride;
        _tokenOverride = (connector.Id,
            string.IsNullOrEmpty(apiToken) ? null : apiToken, connector.AuthHeader);
        using var grant = _candidates.Grant(ToDeclaration(connector), evidence);
        try
        {
            using var dialCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            dialCts.CancelAfter(_dialTimeout);
            var result = await gateway.ExecuteOwnedAsync(
                new McpOperationRequest
                {
                    ConnectorId = connector.Id,
                    Operation = McpOperationKind.ToolsList
                },
                $"connector:test:{connector.Id}",
                dialCts.Token);
            if (result.Status != McpOperationStatus.Succeeded)
                return ConnectorTestResult.Fail(
                    result.FailureReason ?? "The server could not be reached.");
            return ConnectorTestResult.Ok(CountTools(result.Content));
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                       or OperationCanceledException or McpProtocolException
                                       or JsonException or IOException
                                       or InvalidOperationException)
        {
            return ConnectorTestResult.Fail(ex.Message);
        }
        finally
        {
            _tokenOverride = previousOverride;
            await ReleaseSessionAsync(connector.Id);
        }
    }

    private static int CountTools(string content) => ParseToolList(content).Count;

    /// <summary>
    /// The gateway hands back everything a server said wrapped as an untrusted
    /// observation, so the listing has to be taken out of the envelope rather
    /// than scanned for. Scanning would find the envelope's own arrays first,
    /// which is how a tools/list can look empty while the connector is healthy.
    /// </summary>
    private static string Unwrap(string content) =>
        Application.Services.UntrustedObservationBoundary.TryReadEnvelope(
            content, out var envelope) && envelope is not null
            ? envelope.Content
            : content;

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

        lock (_remoteLock)
        {
            _remoteTools.Remove(connectorId);
            RebuildToolOwners();
            _status.Remove(connectorId);
        }
        await ReleaseSessionAsync(connectorId);
        // The approval goes with the install. Keeping it would mean reinstalling
        // the same id later silently inherits a decision made about whatever was
        // there before.
        if (_trustStore is not null)
            await _trustStore.RemoveAsync(connectorId, ct);
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
            await ReleaseSessionAsync(connectorId);
            SetStatus(connectorId, ConnectorStatus.Disconnected);
        }
    }

    /// <summary>
    /// One connector's dial, as a durable operation: tools/list through the
    /// gateway, which means it is preceded by a trust check and followed by an
    /// AgentStep. A connector that is installed but not approved comes back
    /// Blocked and shows as NotConfigured — the state that tells the user there
    /// is a decision to make, rather than Disconnected, which suggests a
    /// network problem they cannot fix.
    /// </summary>
    private async Task ConnectOneAsync(string connectorId, CancellationToken ct)
    {
        var connector = await FindConnectorAsync(connectorId, ct);
        if (connector is null || !HasEndpoint(connector))
        {
            SetStatus(connectorId, ConnectorStatus.NotConfigured);
            return;
        }
        if (_gateway?.Invoke() is not { } gateway)
        {
            SetStatus(connectorId, ConnectorStatus.NotConfigured);
            return;
        }

        try
        {
            var result = await gateway.ExecuteOwnedAsync(
                new McpOperationRequest
                {
                    ConnectorId = connectorId,
                    Operation = McpOperationKind.ToolsList
                },
                $"connector:dial:{connectorId}",
                ct);

            if (result.Status != McpOperationStatus.Succeeded)
            {
                lock (_remoteLock)
                {
                    _remoteTools.Remove(connectorId);
                    RebuildToolOwners();
                }
                _logger.LogInformation(
                    "Connector {Id} not available: {Code} — {Reason}",
                    connectorId, result.FailureCode, result.FailureReason);
                SetStatus(connectorId, StatusForRefusal(connectorId, result));
                return;
            }

            var tools = ParseToolList(result.Content);
            lock (_remoteLock)
            {
                _remoteTools[connectorId] = tools;
                RebuildToolOwners();
            }
            SetStatus(connectorId, ConnectorStatus.Connected);
            _logger.LogInformation("Connector {Id}: {Count} remote tools", connectorId, tools.Count);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                       or McpProtocolException or JsonException or IOException
                                       or InvalidOperationException)
        {
            _logger.LogWarning(ex, "Connector {Id} unreachable", connectorId);
            await ReleaseSessionAsync(connectorId);
            SetStatus(connectorId, FailureStatus(connectorId, ex));
        }
    }

    /// <summary>A refusal that a person can act on (approve the connector, or
    /// re-approve it after it changed) is not the same as a server being down,
    /// and showing both as Disconnected hides the one the user can fix.</summary>
    private static ConnectorStatus StatusForRefusal(
        string connectorId, McpOperationResult result) =>
        result.FailureCode is "ConnectorUntrusted" or "ConnectorAwaitingTrust"
            or "ConnectorNotInstalled" or "ConnectorDisabled" or "ConnectorRevoked"
            or "ConnectorPermissionExpanded" or "ConnectorExecutableChanged"
            or "ConnectorManifestChanged" or "ConnectorEndpointChanged"
            or "ConnectorIdentityUnknown"
            ? ConnectorStatus.NotConfigured
            : ConnectorStatus.Disconnected;

    /// <summary>
    /// The gateway returns tools/list as an untrusted observation wrapping a JSON
    /// array of name/description. Schemas are not carried through it, so a tool
    /// with no schema gets the empty object — <see cref="McpSchemaValidator"/>
    /// then decides whether that is offerable, exactly as before.
    /// </summary>
    private static List<McpToolDefinition> ParseToolList(string content)
    {
        var tools = new List<McpToolDefinition>();
        try
        {
            var listing = Unwrap(content);
            var start = listing.IndexOf('[');
            var end = listing.LastIndexOf(']');
            if (start < 0 || end <= start)
                return tools;
            using var document = JsonDocument.Parse(listing[start..(end + 1)]);
            if (document.RootElement.ValueKind != JsonValueKind.Array)
                return tools;
            foreach (var element in document.RootElement.EnumerateArray())
            {
                var name = element.TryGetProperty("Name", out var nameElement)
                           || element.TryGetProperty("name", out nameElement)
                    ? nameElement.GetString()
                    : null;
                if (string.IsNullOrWhiteSpace(name))
                    continue;
                var description =
                    element.TryGetProperty("Description", out var descriptionElement)
                    || element.TryGetProperty("description", out descriptionElement)
                        ? descriptionElement.GetString()
                        : null;
                var schema = element.TryGetProperty("InputSchema", out var schemaElement)
                             || element.TryGetProperty("inputSchema", out schemaElement)
                    ? schemaElement.GetRawText()
                    : "{\"type\":\"object\",\"properties\":{}}";
                tools.Add(new McpToolDefinition
                {
                    Name = name!,
                    Description = description,
                    InputSchema = schema
                });
            }
        }
        catch (JsonException)
        {
        }
        return tools;
    }

    private async Task ReleaseSessionAsync(string connectorId)
    {
        if (_sessions?.Invoke() is not { } sessions)
            return;
        try
        {
            await sessions.ReleaseAsync(connectorId);
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException
                                       or ObjectDisposedException)
        {
            _logger.LogDebug(ex, "Connector {Id} session release failed", connectorId);
        }
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
    internal Func<string?> ResolveTokenProvider(McpConnector connector)
    {
        if (connector.AuthType == "none")
        {
            // Custom-connector guard: only CATALOG auth-none connectors (the
            // built-in gateway servers) get the portal JWT. A USER-ADDED
            // auth-none server is an arbitrary URL — it must receive nothing.
            var isCustom = LoadState().FirstOrDefault(c => c.Id == connector.Id)?.Custom is not null;
            return isCustom ? () => null : () => _tokenSession?.AccessToken;
        }
        return ResolveTokenProvider(connector.Id);
    }

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

    public IReadOnlyList<GatewayToolDefinition> GetCachedRemoteToolDefinitions()
    {
        lock (_remoteLock)
        {
            var defs = new List<GatewayToolDefinition>();
            var seenNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var (_, tools) in _remoteTools)
            {
                foreach (var tool in tools)
                {
                    // A schema we cannot validate means we cannot constrain what
                    // this server is asked to do. Drop the tool rather than
                    // substituting a permissive object schema, which would hand
                    // the server an unvalidated payload channel.
                    var verdict = McpSchemaValidator.Validate(tool.InputSchema);
                    if (!verdict.Accepted)
                    {
                        _logger.LogWarning(
                            "Dropping MCP tool {Tool}: {Code} — {Reason}",
                            tool.Name,
                            verdict.RejectionCode,
                            verdict.Reason);
                        continue;
                    }
                    if (!seenNames.Add(tool.Name))
                    {
                        // Two connectors claiming one name would let the later
                        // registration shadow the tool the user actually trusted.
                        _logger.LogWarning(
                            "Dropping MCP tool {Tool}: the name is already claimed "
                            + "by another connector", tool.Name);
                        continue;
                    }
                    using var schemaDocument = JsonDocument.Parse(tool.InputSchema);
                    var schema = schemaDocument.RootElement.Clone();
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

    public IReadOnlyList<string> GetCachedToolNames(string connectorId)
    {
        lock (_remoteLock)
        {
            return _remoteTools.TryGetValue(connectorId, out var tools)
                ? tools.Select(t => t.Name).ToList()
                : Array.Empty<string>();
        }
    }

    public bool TryResolveRemoteTool(string toolName, out string connectorId)
    {
        lock (_remoteLock)
        {
            return _toolOwner.TryGetValue(toolName, out connectorId!);
        }
    }

    /// <summary>
    /// A model's call to a connector tool. It attaches to the AgentStep the
    /// orchestrator already prepared for this tool call, so the operation is one
    /// step rather than two, and inherits everything the gateway does around it:
    /// trust re-validation, risk classification from the approved policy, an
    /// intent recorded before dispatch, and reconciliation instead of a retry
    /// when the answer is lost.
    /// </summary>
    public async Task<ToolResult> ExecuteRemoteToolAsync(
        string toolName, JsonElement arguments, CancellationToken ct = default)
    {
        if (!TryResolveRemoteTool(toolName, out var connectorId))
            return ToolResult.Fail($"Unknown remote tool '{toolName}'.");
        if (_gateway?.Invoke() is not { } gateway)
            return ToolResult.Fail(
                "The connector runtime is unavailable, so the tool cannot run.");

        var result = await gateway.ExecuteAttachedAsync(
            new McpOperationRequest
            {
                ConnectorId = connectorId,
                Operation = McpOperationKind.ToolsCall,
                ToolName = toolName,
                ArgumentsJson = arguments.ValueKind == JsonValueKind.Undefined
                    ? "{}"
                    : arguments.GetRawText()
            },
            $"connector:{connectorId}",
            ct);

        SetStatus(connectorId, result.Status switch
        {
            McpOperationStatus.Succeeded => ConnectorStatus.Connected,
            McpOperationStatus.Blocked => StatusForRefusal(connectorId, result),
            _ => GetStatus(connectorId)
        });

        return result.Status switch
        {
            McpOperationStatus.Succeeded => ToolResult.Ok(result.Content),

            // The operation reached the connector and nobody can say what it did.
            // Reporting a plain failure would invite the model to try again, which
            // is the one thing that must not happen.
            McpOperationStatus.NeedsUser or McpOperationStatus.UnknownOutcome =>
                ToolResult.FailedEffect(
                    result.FailureCode ?? "UnknownOutcome",
                    result.FailureReason
                    ?? "The connector operation's outcome could not be established. Do not "
                    + "repeat it — it needs a person to confirm what happened.",
                    retryable: false),

            _ => ToolResult.Fail(
                result.FailureReason ?? $"The {connectorId} connector is unavailable.")
        };
    }

    public async Task<bool> PingAsync(string connectorId, CancellationToken ct = default)
    {
        var connector = await FindConnectorAsync(connectorId, ct);
        if (connector is null || !HasEndpoint(connector))
        {
            SetStatus(connectorId, ConnectorStatus.NotConfigured);
            return false;
        }
        await ConnectOneAsync(connectorId, ct);
        return GetStatus(connectorId) == ConnectorStatus.Connected;
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

    // ── Endpoint identity and credentials (IConnectorEndpointAuthority) ─────

    /// <summary>Token for a connector being tested before install, which has no
    /// DPAPI entry yet. Scoped to one TestConnectionAsync call.</summary>
    private (string ConnectorId, string? Token, string? AuthHeader)? _tokenOverride;

    /// <summary>Header values resolved at session start, one per connector.
    /// Holding the composed header rather than the token means nothing here is a
    /// credential anybody could reuse for a different scheme or endpoint.</summary>
    private readonly Dictionary<string, string?> _preparedHeaders = new(StringComparer.Ordinal);

    /// <summary>
    /// Resolves the credential for the connector's selected account before a
    /// session is created, refreshing an expired OAuth token if it can.
    ///
    /// A failure here is deliberately not thrown: the connector goes on without
    /// an Authorization header, the server answers 401, and that surfaces through
    /// the ordinary status path as a connector needing attention. Throwing would
    /// turn "your account needs reconnecting" into a crash in a background sweep.
    /// </summary>
    public async Task PrepareEndpointAsync(
        string connectorId, CancellationToken ct = default)
    {
        if (_broker is null)
            return;

        if (ResolveAccount(connectorId) is not { } account)
        {
            // No usable account — because it was revoked, disconnected, or was
            // never there. Whatever the reason, any credential cached from an
            // earlier preparation has to go: leaving it would mean revoking an
            // account stops nothing until the session happens to be rebuilt,
            // which is the opposite of what revoking is for.
            lock (_remoteLock)
                _preparedHeaders.Remove(connectorId);
            return;
        }

        var lease = await _broker.LeaseAsync(account, RequiredScopesFor(connectorId), ct);
        lock (_remoteLock)
        {
            if (lease.Granted)
            {
                using (lease.Lease)
                    _preparedHeaders[connectorId] = lease.Lease!.HeaderValue;
            }
            else
            {
                _preparedHeaders.Remove(connectorId);
                _logger.LogInformation(
                    "Connector {Id} account {Account} is not usable: {Failure} — {Reason}",
                    connectorId, account.AccountId, lease.Failure, lease.Reason);
            }
        }
    }

    /// <summary>
    /// Which account this connector acts as.
    ///
    /// An explicit choice wins. With none recorded, exactly one connected
    /// account resolves; two or more is ambiguous and returns null, because
    /// picking one for the user is how a message gets sent from the wrong
    /// mailbox.
    /// </summary>
    internal ConnectorAccountRef? ResolveAccount(string connectorId)
    {
        if (_broker is null)
            return null;
        var record = LoadState().FirstOrDefault(item => item.Id == connectorId);
        var tenant = record?.TenantId ?? ConnectorAccountRef.DefaultTenant;
        if (record?.AccountId is { Length: > 0 } accountId)
            return new ConnectorAccountRef(connectorId, tenant, accountId);

        var connected = _broker.ListAccounts(connectorId, tenant)
            .Where(item => item.State != ConnectorCredentialState.Revoked)
            .ToList();
        return connected.Count == 1 ? connected[0].Account : null;
    }

    /// <summary>Scopes an operation on this connector needs. Catalog-declared
    /// today; the point is that the broker refuses a lease that does not cover
    /// them rather than discovering it at the provider.</summary>
    private IReadOnlyList<string> RequiredScopesFor(string connectorId) =>
        _catalog?.FirstOrDefault(entry => entry.Id == connectorId)?.Scopes ?? [];

    public Func<string?> TokenProviderFor(string connectorId)
    {
        if (_tokenOverride is { } candidate
            && string.Equals(candidate.ConnectorId, connectorId, StringComparison.Ordinal))
            return () => candidate.Token;

        var installed = LoadState().FirstOrDefault(record => record.Id == connectorId);
        var custom = installed?.Custom;

        // A connected account comes first. The user explicitly bound that account
        // TO this connector, so its credential is the one the connector is meant
        // to act as — regardless of what the definition's auth_type says. Read
        // under the lock on each request so a refresh between requests is
        // picked up.
        if (_broker is not null && ResolveAccount(connectorId) is not null)
            return () =>
            {
                lock (_remoteLock)
                    return _preparedHeaders.GetValueOrDefault(connectorId);
            };

        if (custom is not null && custom.AuthType == "none")
            // A user-added auth-none server with no connected account is an
            // arbitrary URL. It receives nothing — falling through to the portal
            // JWT below would hand a third party a credential for this user's
            // account, which is a different thing entirely from the deliberate
            // account binding handled above.
            return () => null;

        var catalogEntry = _catalog?.FirstOrDefault(entry => entry.Id == connectorId);
        if (custom is null && catalogEntry?.AuthType == "none")
            return () => _tokenSession?.AccessToken;

        return ResolveTokenProvider(connectorId);
    }

    public string? AuthHeaderFor(string connectorId)
    {
        if (_tokenOverride is { } candidate
            && string.Equals(candidate.ConnectorId, connectorId, StringComparison.Ordinal))
            return candidate.AuthHeader;
        var installed = LoadState().FirstOrDefault(record => record.Id == connectorId);
        return installed?.Custom?.AuthHeader
               ?? _catalog?.FirstOrDefault(entry => entry.Id == connectorId)?.AuthHeader;
    }

    /// <summary>
    /// Marketplace connectors as declarations the resolver can gate.
    ///
    /// They have no package directory and no manifest file, so the declaration is
    /// derived from the install record and the catalog entry. What matters is
    /// that they end up in the SAME trust pipeline: an HTTP connector's identity
    /// is its endpoint rather than a hash, but it is still recorded at approval
    /// and still re-checked at every launch.
    /// </summary>
    public IReadOnlyList<ConnectorDeclaration> InstalledDeclarations()
    {
        var declarations = new List<ConnectorDeclaration>();
        foreach (var record in LoadState())
        {
            var definition = record.Custom
                             ?? _catalog?.FirstOrDefault(entry => entry.Id == record.Id);
            if (definition is null || definition.Transport == McpConnector.TransportStdio)
                // A stdio connector is a local executable. It belongs in the
                // controlled installation root with a real manifest, not in a
                // marketplace record — and until it is there it cannot run.
                continue;
            declarations.Add(ToDeclaration(
                definition,
                record.Custom is not null ? "user-supplied" : "catalog"));
        }
        return declarations;
    }

    internal static ConnectorDeclaration ToDeclaration(
        McpConnector connector, string installSource = "user-supplied") => new()
    {
        ConnectorId = connector.Id,
        Name = connector.Name,
        // The catalog carries no version. Deriving one from the endpoint means a
        // connector repointed at a different server reads as a different version,
        // which is exactly the invalidation that should happen.
        Version = VersionFor(connector),
        Publisher = null,
        Description = connector.Description,
        Transport = ConnectorTransportKind.Http,
        ServerUrl = connector.ServerUrl,
        InstallSource = installSource,
        Permissions = new ConnectorPermissionSet
        {
            Capabilities = ["network"],
            RequiresNetwork = true,
            CredentialRequirements = connector.AuthType == "none"
                ? []
                : [$"{connector.Id}:{connector.AuthType}"]
        },
        ToolCapabilities = connector.Tools,
        RiskSummary =
            $"Remote MCP server at {connector.ServerUrl}. Its tools run on that server and "
            + "their side effects happen outside this machine."
    };

    private static string VersionFor(McpConnector connector) =>
        $"remote-{Application.Services.SensitiveDataRedactor.Fingerprint(connector.ServerUrl)}";

    // ── Install state ───────────────────────────────────────────────────────

    private List<InstalledConnector> LoadState()
    {
        if (_installed is not null)
            return _installed;
        try
        {
            if (File.Exists(_stateFile))
            {
                var state = JsonSerializer.Deserialize<List<InstalledConnector>>(
                    File.ReadAllText(_stateFile)) ?? new();
                var scrubbedLegacyEnvironment = false;
                foreach (var record in state)
                {
                    if (record.Custom?.Transport == McpConnector.TransportStdio
                        && record.Custom.Env is { Count: > 0 })
                    {
                        record.Custom.Env = null;
                        scrubbedLegacyEnvironment = true;
                    }
                    if (record.Custom?.Transport == McpConnector.TransportStdio
                        && record.Custom.Args is { Count: > 0 } arguments)
                    {
                        var redacted = arguments
                            .Select(Application.Services.SensitiveDataRedactor.Redact)
                            .ToList();
                        if (!arguments.SequenceEqual(redacted, StringComparer.Ordinal))
                        {
                            record.Custom.Args = redacted;
                            scrubbedLegacyEnvironment = true;
                        }
                    }
                }
                if (scrubbedLegacyEnvironment)
                {
                    SaveState(state);
                    _logger.LogWarning(
                        "Removed legacy local MCP environment values from connector state");
                }
                return _installed = state;
            }
        }
        catch (Exception ex) when (ex is JsonException or IOException)
        {
            _logger.LogWarning(ex, "connectors.json unreadable");
        }
        return _installed = new List<InstalledConnector>();
    }

    /// <summary>Disk first, cache second — see JsonConnectorTrustStore.Persist.
    /// An install that failed to write must not look installed until restart.</summary>
    private void SaveState(List<InstalledConnector> state)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(_stateFile)!);
        Storage.AtomicFile.Write(_stateFile, JsonSerializer.Serialize(state, JsonOptions));
        _installed = state;
    }

    public ValueTask DisposeAsync()
    {
        // Sessions belong to the gateway's session provider now, which owns their
        // processes and job objects; disposing them from here would race it.
        _healthTimer?.Dispose();
        _catalogHttp.Dispose();
        return ValueTask.CompletedTask;
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
