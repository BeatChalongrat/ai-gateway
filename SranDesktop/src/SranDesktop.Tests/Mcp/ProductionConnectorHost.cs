using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using SranDesktop.App.Hosting;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// The application's REAL service graph, pointed at a throwaway data directory.
///
/// This exists because the claim Phase 4.1C has to support is not "a durable MCP
/// path can be assembled" — 4.1B already showed that with a hand-built graph —
/// but "the path the application composes is that path". A test that wires its
/// own gateway proves nothing about what happens when the user launches the app,
/// which is exactly how the production resolver came to return null for every
/// connector while every test passed.
///
/// So this builds <see cref="AppHostBuilder"/> itself, and the only thing it
/// overrides is where files go.
/// </summary>
public sealed class ProductionConnectorHost : IAsyncDisposable
{
    private readonly IHost _host;

    public ProductionConnectorHost(string root, params string[] extraArguments)
    {
        Root = root;
        ConnectorRoot = Path.Combine(root, "connectors");
        DataDirectory = Path.Combine(root, "data");
        Directory.CreateDirectory(ConnectorRoot);
        Directory.CreateDirectory(DataDirectory);

        _host = AppHostBuilder.Build(
        [
            $"--Sran:ConnectorRoot={ConnectorRoot}",
            $"--Sran:DataDirectory={DataDirectory}",
            $"--Sran:ConnectorTrustFile={Path.Combine(DataDirectory, "connector-trust.json")}",
            .. extraArguments
        ]);
    }

    public string Root { get; }
    public string ConnectorRoot { get; }
    public string DataDirectory { get; }

    public IServiceProvider Services => _host.Services;

    public IMcpDurableGateway Gateway => Services.GetRequiredService<IMcpDurableGateway>();

    public IConnectorManifestResolver Resolver =>
        Services.GetRequiredService<IConnectorManifestResolver>();

    public IConnectorTrustStore TrustStore => Services.GetRequiredService<IConnectorTrustStore>();

    public IConnectorTrustAdministration Trust =>
        Services.GetRequiredService<IConnectorTrustAdministration>();

    public IMcpDispatchJournal Journal => Services.GetRequiredService<IMcpDispatchJournal>();

    public IAgentTaskStore Tasks => Services.GetRequiredService<IAgentTaskStore>();

    public IDurableTaskRuntime Runtime => Services.GetRequiredService<IDurableTaskRuntime>();

    public IMcpConnectorService Connectors => Services.GetRequiredService<IMcpConnectorService>();

    /// <summary>
    /// Stops sessions and releases the sqlite handles. Disposing the host is what
    /// a real shutdown does; the tests that restart the runtime depend on it
    /// actually letting go of the database file.
    /// </summary>
    public async ValueTask DisposeAsync()
    {
        if (Services.GetService<SranDesktop.Infrastructure.Mcp.Remote
                .IMcpConnectorSessionProvider>() is IAsyncDisposable sessions)
            await sessions.DisposeAsync();
        if (Services.GetService<IMcpConnectorService>() is IAsyncDisposable connectors)
            await connectors.DisposeAsync();
        _host.Dispose();

        // Deliberately NOT SqliteConnection.ClearAllPools(): that is process-wide,
        // and a teardown that reaches into every other test's connection pool
        // turns one class's cleanup into another class's flake. The patient delete
        // below waits the handles out instead.
    }

    /// <summary>
    /// Removes a test root, waiting out the handles that are still closing.
    ///
    /// Teardown races with two things that let go on their own schedule: the
    /// sandbox job object releasing the connector image it just ran, and the
    /// virus scanner reading the DLLs that were copied a moment ago. A single
    /// delete attempt loses that race often enough to leave directories behind —
    /// which then read as a leak in the very measurement these tests exist to
    /// make honest.
    /// </summary>
    public static async Task DeletePatientlyAsync(string root)
    {
        for (var attempt = 0; attempt < 60; attempt++)
        {
            try
            {
                if (!Directory.Exists(root))
                    return;
                Directory.Delete(root, recursive: true);
                return;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                if (attempt == 4)
                {
                    // Finalizers hold FileStreams that nobody disposed; run them
                    // once rather than every time round.
                    GC.Collect();
                    GC.WaitForPendingFinalizers();
                }
                // No ClearAllPools fallback any more: the durable stores open
                // with Pooling=false, so a disposed connection has already
                // released the file. Anything still holding it here is a real
                // owner — a worker process, a scanner — and waiting is the
                // honest response, not reaching into a pool that is empty.
                await Task.Delay(200);
            }
        }
    }
}

/// <summary>
/// Builds a connector PACKAGE the way a real one is installed: a directory in
/// the managed root holding an executable and a manifest that declares what it
/// wants.
///
/// The executable is the fixture MCP server, copied rather than referenced, so a
/// test can replace it afterwards and watch the trust decision come apart —
/// which is the point of hashing it in the first place.
/// </summary>
public static class ConnectorPackage
{
    public const string ServerFileName = "SranDesktop.McpFixtureServer.dll";

    public static string SourceImage =>
        Path.Combine(AppContext.BaseDirectory, "workers", "mcp-fixture");

    /// <summary>
    /// Installs the fixture server as connector <paramref name="connectorId"/>.
    /// Nothing here approves it: a freshly installed package is untrusted, and
    /// making the install step also grant trust would remove the only decision
    /// this phase is about.
    /// </summary>
    public static ConnectorDeclaration Install(
        string connectorRoot,
        string connectorId,
        Action<ManifestBuilder>? configure = null)
    {
        var directory = Path.Combine(connectorRoot, connectorId);
        Directory.CreateDirectory(directory);
        CopyImage(SourceImage, directory);

        var builder = new ManifestBuilder(connectorId, ServerFileName);
        configure?.Invoke(builder);
        var manifestPath = Path.Combine(directory, "connector.manifest.json");
        File.WriteAllText(manifestPath, builder.ToJson());

        return SranDesktop.Infrastructure.Mcp.Connectors.ConnectorManifestReader
            .Read(manifestPath, connectorId, directory).Declaration
            ?? throw new InvalidOperationException(
                "The package fixture wrote a manifest its own reader rejects.");
    }

    public static string ExecutablePath(string connectorRoot, string connectorId) =>
        Path.Combine(connectorRoot, connectorId, ServerFileName);

    public static void RewriteManifest(
        string connectorRoot, string connectorId, Action<ManifestBuilder> configure)
    {
        var builder = new ManifestBuilder(connectorId, ServerFileName);
        configure(builder);
        File.WriteAllText(
            Path.Combine(connectorRoot, connectorId, "connector.manifest.json"),
            builder.ToJson());
    }

    private static void CopyImage(string source, string destination)
    {
        foreach (var file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(source, file);
            var target = Path.Combine(destination, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
    }

    public sealed class ManifestBuilder(string connectorId, string executable)
    {
        public string ConnectorId { get; set; } = connectorId;
        public string Name { get; set; } = "Fixture Connector";
        public string Version { get; set; } = "1.0.0";
        public string Publisher { get; set; } = "SRAN Test Fixtures";
        public string Executable { get; set; } = executable;
        public string InstallSource { get; set; } = "local-package";
        public List<string> Capabilities { get; set; } = ["tools", "resources", "prompts"];
        public List<string> WorkspaceAccess { get; set; } = ["connector-workspace"];
        public bool RequiresNetwork { get; set; }
        public List<string> Credentials { get; set; } = [];
        public List<string> Tools { get; set; } =
            ["ping", "get_record", "create_record", "lookup_by_idempotency_key", "stats"];
        public List<string> Resources { get; set; } = ["fixture://readme"];
        public List<string> Prompts { get; set; } = ["summarize"];
        public List<string> ReadOnlyTools { get; set; } =
            ["ping", "get_record", "lookup_by_idempotency_key", "stats"];
        public List<string> ExternalMutationTools { get; set; } = ["create_record"];
        public Dictionary<string, string> VerificationTools { get; set; } =
            new(StringComparer.OrdinalIgnoreCase) { ["create_record"] = "get_record" };
        public string RiskSummary { get; set; } =
            "Creates records on a fixture backend. create_record has an effect that "
            + "survives the call.";

        /// <summary>Raw override, for the malformed-manifest tests.</summary>
        public string? RawJson { get; set; }

        public string ToJson() => RawJson ?? JsonSerializer.Serialize(new
        {
            connector_id = ConnectorId,
            name = Name,
            version = Version,
            publisher = Publisher,
            description = "Deterministic MCP server used to exercise the runtime.",
            transport = "stdio",
            executable = Executable,
            install_source = InstallSource,
            protocol_version = "2025-06-18",
            capabilities = Capabilities,
            workspace_access = WorkspaceAccess,
            requires_network = RequiresNetwork,
            credentials = Credentials,
            tools = Tools,
            resources = Resources,
            prompts = Prompts,
            risk_summary = RiskSummary,
            read_only_tools = ReadOnlyTools,
            external_mutation_tools = ExternalMutationTools,
            verification_tools = VerificationTools
        }, new JsonSerializerOptions { WriteIndented = true });
    }
}
