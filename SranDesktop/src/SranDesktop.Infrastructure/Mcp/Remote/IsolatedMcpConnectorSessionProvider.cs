using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Tools;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// Turns an approved connector manifest into a live transport. Split from the
/// session provider so a test can drive a real server without the install
/// layout, and so there is exactly one place where a connector becomes a running
/// process or an open connection.
/// </summary>
public interface IMcpServerLauncher
{
    Task<IMcpTransport> LaunchAsync(
        ConnectorManifest manifest,
        CancellationToken ct = default);
}

/// <summary>
/// Supplies the Bearer source for an HTTP connector. Kept as an interface so the
/// session provider never reaches into credential storage itself: the thing that
/// launches servers and the thing that holds secrets are deliberately different
/// objects.
/// </summary>
public interface IConnectorEndpointAuthority
{
    /// <summary>
    /// Resolves the credential for the connector's currently selected account and
    /// caches it for the session about to be created.
    ///
    /// This is async and separate from <see cref="TokenProviderFor"/> because
    /// resolving may have to refresh an expired OAuth token, and the transport's
    /// per-request token callback is synchronous. Doing the refresh here — once,
    /// at session start, on a path that is already async — keeps the sync-over-
    /// async deadlock out of the request path entirely.
    /// </summary>
    Task PrepareEndpointAsync(string connectorId, CancellationToken ct = default);

    /// <summary>The header VALUE for the prepared account, resolved per request
    /// so a refresh between requests is picked up. Never the raw token when the
    /// scheme wants a prefix — the whole header value or nothing.</summary>
    Func<string?> TokenProviderFor(string connectorId);

    string? AuthHeaderFor(string connectorId);
}

/// <summary>
/// Launches a local MCP server inside the capability-free AppContainer from
/// Phase 4.1A, or opens a streamable-HTTP connection for a remote one. The image
/// directory is derived from the APPROVED manifest — the same manifest whose
/// hash and location the trust policy just checked — never from anything a model
/// or a server said.
/// </summary>
public sealed class IsolatedMcpServerLauncher : IMcpServerLauncher
{
    private readonly string _workspaceRoot;
    private readonly Func<IConnectorEndpointAuthority?>? _endpoints;
    private readonly McpEndpointPolicy _endpointPolicy;
    private readonly ILogger? _logger;

    public IsolatedMcpServerLauncher(
        string workspaceRoot,
        Func<IConnectorEndpointAuthority?>? endpoints = null,
        ILogger? logger = null,
        McpEndpointPolicy? endpointPolicy = null)
    {
        _workspaceRoot = workspaceRoot;
        _endpoints = endpoints;
        _endpointPolicy = endpointPolicy ?? McpEndpointPolicy.Default;
        _logger = logger;
    }

    public async Task<IMcpTransport> LaunchAsync(
        ConnectorManifest manifest,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(manifest);

        if (manifest.Transport == ConnectorTransportKind.Http)
        {
            // The endpoint is checked HERE — at launch, against the address the
            // request is about to go to — and before any credential is resolved.
            // Checking once at install would miss a catalog refresh or an edited
            // definition that moved the connector after it was approved.
            var verdict = _endpointPolicy.Evaluate(manifest.ServerUrl);
            if (!verdict.Allowed)
            {
                _logger?.LogWarning(
                    "Connector {Id} endpoint refused: {Code}",
                    manifest.ConnectorId, verdict.RejectionCode);
                throw new McpProtocolException(
                    $"{verdict.RejectionCode}: {verdict.Reason}");
            }

            var authority = _endpoints?.Invoke();
            return new HttpMcpTransport(
                manifest.ServerUrl,
                authority?.TokenProviderFor(manifest.ConnectorId),
                authHeader: authority?.AuthHeaderFor(manifest.ConnectorId),
                maximumResponseBytes: _endpointPolicy.MaximumResponseBytes);
        }

        var workspace = Path.Combine(_workspaceRoot, manifest.ConnectorId);
        Directory.CreateDirectory(workspace);

        var directory = Path.GetDirectoryName(manifest.ExecutablePath)
                        ?? throw new SandboxUnavailableException(
                            "The approved connector executable has no containing directory.");
        var runner = new WindowsAppContainerProcessRunner(
            // Sandbox-owned: this directory was created two lines up, and with
            // the default connector root it sits inside the app's state tree,
            // which the user-grant filter would otherwise drop.
            WorkspaceFileBroker.ForSandboxWorkspace(workspace));
        var launch = new IsolatedWorkerLaunch(
            manifest.ConnectorId, directory, Path.GetFileName(manifest.ExecutablePath));
        return new IsolatedStdioMcpTransport(runner, launch, workspace, _logger);
    }
}

/// <summary>
/// Keeps one initialized session per connector and hands it out.
///
/// A reconnect deliberately builds a NEW transport rather than reviving the old
/// one: the generation must go up so a late frame from the dead server can never
/// be mistaken for an answer, and initialize must run again so capabilities and
/// server identity are re-established rather than remembered.
///
/// The manifest is re-resolved on every acquisition. Holding onto the one that
/// was approved would mean a connector whose binary was swapped, whose trust was
/// revoked, or which now asks for more than it was granted keeps running on the
/// strength of a check that happened once.
/// </summary>
public sealed class IsolatedMcpConnectorSessionProvider
    : IMcpConnectorSessionProvider, IAsyncDisposable
{
    private readonly Func<string, ConnectorManifest?> _manifests;
    private readonly Func<string, ConnectorRiskPolicy> _policies;
    private readonly IMcpServerLauncher _launcher;
    private readonly IConnectorManifestResolver? _resolver;
    private readonly IConnectorTrustStore? _trust;
    private readonly Func<IConnectorEndpointAuthority?>? _endpointAuthority;
    private readonly ILogger? _logger;
    private readonly SemaphoreSlim _mutex = new(1, 1);
    private readonly Dictionary<string, Live> _live = new(StringComparer.Ordinal);
    private long _generation;

    public IsolatedMcpConnectorSessionProvider(
        Func<string, ConnectorManifest?> manifests,
        Func<string, ConnectorRiskPolicy> policies,
        IMcpServerLauncher launcher,
        ILogger? logger = null)
    {
        _manifests = manifests;
        _policies = policies;
        _launcher = launcher;
        _logger = logger;
    }

    /// <summary>
    /// The production shape: manifests and risk policies both come from the
    /// resolver, so there is no way to hand this a manifest the trust check never
    /// saw. Subscribing to trust changes is what makes a revocation stop a
    /// session that is already running, rather than only the next one.
    /// </summary>
    public IsolatedMcpConnectorSessionProvider(
        IConnectorManifestResolver resolver,
        IMcpServerLauncher launcher,
        IConnectorTrustStore? trust = null,
        ILogger? logger = null,
        Func<IConnectorEndpointAuthority?>? endpointAuthority = null)
        : this(
            connectorId => resolver.Resolve(connectorId).Descriptor?.Manifest,
            connectorId => resolver.Resolve(connectorId).Descriptor?.RiskPolicy
                           ?? ConnectorRiskPolicy.Undeclared(connectorId),
            launcher,
            logger)
    {
        _resolver = resolver;
        _trust = trust;
        _endpointAuthority = endpointAuthority;
        if (trust is not null)
            trust.TrustChanged += OnTrustChanged;
    }

    /// <summary>Resolution comes from disk on every call. A cached verdict would
    /// mean a connector whose binary was swapped after the last look keeps
    /// running on the strength of a check that is no longer true.</summary>
    public ConnectorResolution Resolve(string connectorId) =>
        _resolver?.Resolve(connectorId)
        ?? McpConnectorResolution.FromManifest(this, connectorId);

    public ConnectorManifest? GetManifest(string connectorId) => _manifests(connectorId);

    public ConnectorRiskPolicy GetRiskPolicy(string connectorId) => _policies(connectorId);

    public async Task<McpConnectorSession> AcquireAsync(
        string connectorId,
        CancellationToken ct = default)
    {
        await _mutex.WaitAsync(ct);
        try
        {
            // Resolve the credential on EVERY acquisition, not only when a
            // transport is built. A session outlives the credential that started
            // it: an account connected afterwards, or a token refreshed since,
            // would otherwise never be picked up and the connector would keep
            // failing with 401 until something happened to drop the session.
            await PrepareCredentialAsync(connectorId, ct);
            if (_live.TryGetValue(connectorId, out var existing)
                && existing.Transport.IsConnected)
                return existing.Session;
            return (await StartLockedAsync(connectorId, ct)).Session;
        }
        finally
        {
            _mutex.Release();
        }
    }

    private async Task PrepareCredentialAsync(string connectorId, CancellationToken ct)
    {
        if (_endpointAuthority?.Invoke() is not { } authority)
            return;
        try
        {
            await authority.PrepareEndpointAsync(connectorId, ct);
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException
                                       or HttpRequestException)
        {
            // An account that cannot be resolved is a connector that will be
            // refused by the server — which surfaces as an authorization failure
            // with a remedy, not as a crash here.
            _logger?.LogWarning(ex,
                "Connector {Id} credential could not be prepared", connectorId);
        }
    }

    public async Task<McpConnectorSession> ReconnectAsync(
        string connectorId,
        CancellationToken ct = default)
    {
        await _mutex.WaitAsync(ct);
        try
        {
            await DropLockedAsync(connectorId);
            await PrepareCredentialAsync(connectorId, ct);
            return (await StartLockedAsync(connectorId, ct)).Session;
        }
        finally
        {
            _mutex.Release();
        }
    }

    public async Task ReleaseAsync(string connectorId)
    {
        await _mutex.WaitAsync();
        try
        {
            await DropLockedAsync(connectorId);
        }
        finally
        {
            _mutex.Release();
        }
    }

    /// <summary>
    /// A revoked or disabled connector must not keep the session it already has.
    /// Waiting for the next resolution would leave a server that the user has
    /// just withdrawn trust from still running, still holding its workspace, and
    /// still able to answer an in-flight request.
    /// </summary>
    private void OnTrustChanged(object? sender, ConnectorTrustChange change)
    {
        if (change.State is ConnectorTrustState.Trusted)
            return;
        _ = Task.Run(async () =>
        {
            try
            {
                await ReleaseAsync(change.ConnectorId);
                _logger?.LogInformation(
                    "Connector {Id} session stopped: trust is now {State}",
                    change.ConnectorId, change.State);
            }
            catch (Exception ex) when (ex is IOException or InvalidOperationException
                                           or ObjectDisposedException)
            {
                _logger?.LogWarning(ex,
                    "Connector {Id} session could not be stopped cleanly after a trust change",
                    change.ConnectorId);
            }
        });
    }

    private async Task<Live> StartLockedAsync(string connectorId, CancellationToken ct)
    {
        var manifest = _manifests(connectorId)
                       ?? throw new InvalidOperationException(
                           $"No approved connector '{connectorId}' is available to launch.");
        var transport = await _launcher.LaunchAsync(manifest, ct);
        var client = new McpRemoteClient(transport);
        try
        {
            await client.InitializeAsync(ct);
        }
        catch
        {
            // A handshake that failed leaves no usable session and must leave no
            // process either.
            await client.DisposeAsync();
            throw;
        }

        // The generation is the provider's, not the transport's: an HTTP
        // connector has no process whose identity could carry one, and a
        // reconnect has to be distinguishable from the session it replaced on
        // every transport, or a late answer from the old one looks current.
        var session = new McpConnectorSession(
            connectorId,
            client,
            transport is IsolatedStdioMcpTransport isolated
                ? isolated.Generation
                : Interlocked.Increment(ref _generation),
            client.ServerIdentity,
            client.Capabilities);
        var live = new Live(transport, client, session);
        _live[connectorId] = live;
        _logger?.LogInformation(
            "Connector {Id} session {Generation} ready ({Server})",
            connectorId, session.Generation, client.ServerIdentity);
        return live;
    }

    private async Task DropLockedAsync(string connectorId)
    {
        if (!_live.Remove(connectorId, out var live))
            return;
        try
        {
            await live.Client.DisposeAsync();
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException
                                       or ObjectDisposedException)
        {
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_trust is not null)
            _trust.TrustChanged -= OnTrustChanged;
        foreach (var connectorId in _live.Keys.ToList())
            await DropLockedAsync(connectorId);
        _mutex.Dispose();
    }

    private sealed record Live(
        IMcpTransport Transport,
        McpRemoteClient Client,
        McpConnectorSession Session);
}
