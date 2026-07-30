using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Connectors;
using Xunit;

namespace SranDesktop.Tests.Security;

/// <summary>
/// An approval is only an approval if it is durable.
///
/// The defect these tests exist for was real and reached a packaged build: the
/// trust store updated its in-memory cache BEFORE writing to disk, so a failed
/// write left the process believing the connector was approved. The card turned
/// green, the resolver answered Trusted, and nothing on disk agreed — the
/// approval then vanished at the next restart with no sign it had been lost.
///
/// That is the "optimistic approval" failure mode: the UI showing a decision the
/// system did not actually record.
/// </summary>
[Collection("PathGuardGrants")]
public sealed class ApprovalPersistenceInvariantTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-approval-persist-{Guid.NewGuid():N}");
    private readonly string _connectorRoot;
    private readonly string _trustFile;

    public ApprovalPersistenceInvariantTests()
    {
        AgentInputScope.ResetForTests();
        _connectorRoot = Path.Combine(_root, "connectors");
        Directory.CreateDirectory(_connectorRoot);
        // The trust file lives in its own subdirectory so a test can make THAT
        // path unwritable without disturbing the connector package, which the
        // approval still has to be able to read.
        _trustFile = Path.Combine(_root, "trust", "trust.json");
    }

    [Fact]
    public async Task An_Approval_That_Cannot_Be_Written_Does_Not_Approve_Anything()
    {
        var (resolver, administration, store) = Build();
        Install("unwritable");

        // The trust file's parent directory is replaced by a FILE, so every write
        // to that path fails the way a locked profile or a full disk would — while
        // the connector package stays readable, which is what makes this a
        // persistence failure rather than a missing connector.
        var trustDirectory = Path.GetDirectoryName(_trustFile)!;
        if (Directory.Exists(trustDirectory))
            Directory.Delete(trustDirectory, recursive: true);
        await File.WriteAllTextAsync(trustDirectory, "not a directory");

        var approve = () => administration.ApproveAsync(
            "unwritable",
            new ConnectorApprovalDecision(ConnectorApprovalOutcome.Approved, "user:test"),
            UserApprovalEvidence.FromUserGesture("user:test", "approval dialog"));

        await approve.Should().ThrowAsync<IOException>(
            "a write that cannot happen must not be reported as success");

        // The store must not have advanced. This is the assertion the bug failed:
        // the cache said Trusted while the disk said nothing.
        store.Get("unwritable").Should().BeNull(
            "the in-memory view must never get ahead of what was persisted");
    }

    [Fact]
    public async Task A_Card_Reads_Its_State_From_Persisted_Trust_Not_From_The_Decision()
    {
        var (resolver, administration, _) = Build();
        Install("reloaded");
        await ApproveAsync(administration, "reloaded");

        // A completely fresh store over the same file — what a restart is. If the
        // approval only existed in the first store's memory, this resolves to
        // untrusted and the card is red.
        var reopened = new JsonConnectorTrustStore(_trustFile, NullLogger.Instance);
        var freshResolver = new ConnectorManifestResolver(
            _connectorRoot, reopened,
            new ConnectorInstallationPolicy([_connectorRoot]), logger: NullLogger.Instance);

        freshResolver.Resolve("reloaded").Resolved.Should().BeTrue(
            "the green state has to survive a process that never saw the click");
        resolver.Resolve("reloaded").Resolved.Should().BeTrue();
    }

    [Fact]
    public async Task Every_Condition_For_A_Green_Card_Is_Required_Individually()
    {
        var (resolver, administration, store) = Build();
        Install("strict");
        await ApproveAsync(administration, "strict");
        resolver.Resolve("strict").Resolved.Should().BeTrue();

        // 1. connector enabled
        var approved = store.Get("strict")!;
        await store.SaveAsync(approved with { Enabled = false });
        resolver.Resolve("strict").Resolved.Should().BeFalse("a disabled connector is not green");
        await store.SaveAsync(approved);

        // 2. executable hash matches
        await File.WriteAllTextAsync(
            Path.Combine(_connectorRoot, "strict", "server.exe"), "different bytes");
        resolver.Resolve("strict").DenialCode.Should().Be("ConnectorExecutableChanged");
        await File.WriteAllTextAsync(
            Path.Combine(_connectorRoot, "strict", "server.exe"), "binary for strict");
        resolver.Resolve("strict").Resolved.Should().BeTrue();

        // 3. permissions have not expanded
        Install("strict", manifest => manifest["requires_network"] = true);
        resolver.Resolve("strict").DenialCode.Should().Be("ConnectorPermissionExpanded");
        Install("strict");
        resolver.Resolve("strict").Resolved.Should().BeTrue();

        // 4. manifest fingerprint matches
        Install("strict", manifest => manifest["version"] = "9.9.9");
        resolver.Resolve("strict").Resolved.Should().BeFalse(
            "a different version is a different thing to have approved");
    }

    [Fact]
    public async Task A_Denial_Is_Persisted_And_Survives_A_Restart()
    {
        var (_, administration, _) = Build();
        Install("refused");

        await administration.ApproveAsync(
            "refused",
            ConnectorApprovalDecision.Deny("the user declined"),
            UserApprovalEvidence.FromAdministrativePolicy("user-denial"));

        File.Exists(_trustFile).Should().BeTrue(
            "a denial is a decision, and a decision that leaves no trace is not one");

        var reopened = new JsonConnectorTrustStore(_trustFile, NullLogger.Instance);
        var record = reopened.Get("refused");
        record.Should().NotBeNull();
        record!.Enabled.Should().BeFalse();
        record.State.Should().Be(ConnectorTrustState.Untrusted);
        record.StateReason.Should().Contain("declined");

        var freshResolver = new ConnectorManifestResolver(
            _connectorRoot, reopened,
            new ConnectorInstallationPolicy([_connectorRoot]), logger: NullLogger.Instance);
        freshResolver.Resolve("refused").Resolved.Should().BeFalse(
            "a denied connector stays denied across a restart");
    }

    [Fact]
    public void The_Trust_Store_Writes_Before_It_Caches()
    {
        // Locked at the source too: the ordering is one line, and reversing it
        // reintroduces the exact failure without breaking anything a functional
        // test would notice on a healthy disk.
        var source = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Infrastructure", "Mcp", "Connectors",
            "JsonConnectorTrustStore.cs"));
        var write = source.IndexOf("AtomicFile.Write(_path", StringComparison.Ordinal);
        var cache = source.LastIndexOf("_records = records;", StringComparison.Ordinal);

        write.Should().BeGreaterThan(0);
        cache.Should().BeGreaterThan(0);
        write.Should().BeLessThan(cache,
            "the in-memory view must only advance after the write succeeded");
    }

    // ── Fixtures ────────────────────────────────────────────────────────────

    private (ConnectorManifestResolver Resolver,
             ConnectorTrustAdministration Administration,
             JsonConnectorTrustStore Store) Build()
    {
        var store = new JsonConnectorTrustStore(_trustFile, NullLogger.Instance);
        var installation = new ConnectorInstallationPolicy([_connectorRoot]);
        var resolver = new ConnectorManifestResolver(
            _connectorRoot, store, installation, logger: NullLogger.Instance);
        return (resolver,
            new ConnectorTrustAdministration(resolver, store, installation, NullLogger.Instance),
            store);
    }

    private static async Task ApproveAsync(
        ConnectorTrustAdministration administration, string connectorId) =>
        await administration.ApproveAsync(
            connectorId,
            new ConnectorApprovalDecision(ConnectorApprovalOutcome.Approved, "user:test"),
            UserApprovalEvidence.FromUserGesture("user:test", "approval dialog"));

    private void Install(string connectorId, Action<System.Text.Json.Nodes.JsonObject>? mutate = null)
    {
        var directory = Path.Combine(_connectorRoot, connectorId);
        Directory.CreateDirectory(directory);
        var executable = Path.Combine(directory, "server.exe");
        if (!File.Exists(executable))
            File.WriteAllText(executable, $"binary for {connectorId}");
        var manifest = new System.Text.Json.Nodes.JsonObject
        {
            ["connector_id"] = connectorId,
            ["name"] = connectorId,
            ["version"] = "1.0.0",
            ["publisher"] = "SRAN Test Fixtures",
            ["transport"] = "stdio",
            ["executable"] = "server.exe",
            ["install_source"] = "local-package",
            ["protocol_version"] = "2025-06-18",
            ["capabilities"] = new System.Text.Json.Nodes.JsonArray("tools"),
            ["workspace_access"] = new System.Text.Json.Nodes.JsonArray("workspace"),
            ["requires_network"] = false,
            ["tools"] = new System.Text.Json.Nodes.JsonArray("ping"),
            ["read_only_tools"] = new System.Text.Json.Nodes.JsonArray("ping")
        };
        mutate?.Invoke(manifest);
        File.WriteAllText(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            manifest.ToJsonString());
    }

    private static string RepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "SranDesktop.sln")))
            directory = directory.Parent;
        return directory?.FullName
               ?? throw new InvalidOperationException("The repository root was not found.");
    }

    public void Dispose()
    {
        try
        {
            // One test replaces the trust directory with a file; clear that
            // first or the recursive delete below trips over it.
            var trustDirectory = Path.GetDirectoryName(_trustFile)!;
            if (File.Exists(trustDirectory))
                File.Delete(trustDirectory);
            if (Directory.Exists(_root))
                Directory.Delete(_root, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
    }
}
