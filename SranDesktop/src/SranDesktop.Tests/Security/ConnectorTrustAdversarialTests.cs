using System.Text.Json;
using System.Text.Json.Nodes;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Connectors;
using Xunit;

namespace SranDesktop.Tests.Security;

/// <summary>
/// Attempts to get a connector running that nobody approved, or to widen one
/// that was.
///
/// These are written against the production resolver, trust store, and
/// administration rather than the gateway, because that is where each of them
/// has to be stopped: by the time the gateway is deciding whether to dispatch,
/// the question "is this the thing the user approved" has already been answered.
/// </summary>
/// <remarks>Shares the "PathGuardGrants" collection with every other class that
/// touches <c>AgentInputScope</c>. That marker is process-wide, and a sibling
/// class resetting it mid-test would clear the very signal these assertions are
/// about — a race that reads as "the guard does not work".</remarks>
[Collection("PathGuardGrants")]
public sealed class ConnectorTrustAdversarialTests : IDisposable
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-trust-adv-{Guid.NewGuid():N}");
    private readonly string _connectorRoot;
    private readonly string _trustFile;
    private readonly JsonConnectorTrustStore _trust;
    private readonly ConnectorManifestResolver _resolver;
    private readonly ConnectorTrustAdministration _administration;
    private readonly ConnectorInstallationPolicy _installation;

    public ConnectorTrustAdversarialTests()
    {
        AgentInputScope.ResetForTests();
        _connectorRoot = Path.Combine(_root, "connectors");
        Directory.CreateDirectory(_connectorRoot);
        _trustFile = Path.Combine(_root, "trust.json");
        _installation = new ConnectorInstallationPolicy([_connectorRoot]);
        _trust = new JsonConnectorTrustStore(_trustFile, NullLogger.Instance);
        _resolver = new ConnectorManifestResolver(
            _connectorRoot, _trust, _installation, logger: NullLogger.Instance);
        _administration = new ConnectorTrustAdministration(
            _resolver, _trust, _installation, NullLogger.Instance);
    }

    // ── Manifest as hostile input ───────────────────────────────────────────

    [Fact]
    public void A_Manifest_Pointing_Outside_Its_Own_Package_Is_Refused()
    {
        Install("traverser", manifest => manifest["executable"] = @"..\..\Windows\System32\cmd.exe");

        var resolution = _resolver.Resolve("traverser");

        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorPathNotControlled");
        resolution.Declaration.Should().BeNull(
            "a declaration that resolves outside its package is not shown to the user "
            + "as if it were part of the package");
    }

    [Fact]
    public void A_Manifest_Naming_A_Different_Connector_Cannot_Borrow_Its_Approval()
    {
        Install("impostor", manifest => manifest["connector_id"] = "trusted-connector");

        _resolver.Resolve("impostor").DenialCode.Should().Be("ConnectorManifestIdMismatch");
    }

    [Fact]
    public void A_Malformed_Manifest_Is_A_Refusal_Not_An_Exception()
    {
        var directory = Path.Combine(_connectorRoot, "broken");
        Directory.CreateDirectory(directory);
        File.WriteAllText(Path.Combine(directory, "server.exe"), "binary");
        File.WriteAllText(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            "{ this is not json");

        _resolver.Resolve("broken").DenialCode.Should().Be("ConnectorManifestMalformed");
        _resolver.Discover().Should().NotContain(item => item.ConnectorId == "broken");
    }

    [Fact]
    public void An_Oversized_Manifest_Is_Rejected_Before_It_Is_Parsed()
    {
        var directory = Path.Combine(_connectorRoot, "huge");
        Directory.CreateDirectory(directory);
        File.WriteAllText(Path.Combine(directory, "server.exe"), "binary");
        File.WriteAllText(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            new string('x', ConnectorManifestReader.MaximumManifestBytes + 1));

        _resolver.Resolve("huge").DenialCode.Should().Be("ConnectorManifestTooLarge");
    }

    [Fact]
    public void A_Manifest_Declaring_An_Unsupported_Protocol_Version_Is_Refused()
    {
        Install("future", manifest => manifest["protocol_version"] = "2099-01-01");

        _resolver.Resolve("future").DenialCode.Should().Be("ConnectorProtocolUnsupported");
    }

    [Fact]
    public void A_Connector_Id_That_Is_A_Path_Cannot_Escape_The_Managed_Root()
    {
        _resolver.Resolve(@"..\..\Windows").DenialCode.Should().Be("ConnectorNotInstalled");
        _resolver.Resolve("a/b").DenialCode.Should().Be("ConnectorNotInstalled");
    }

    [Fact]
    public void The_Sandbox_Workspace_Directory_Is_Not_Mistaken_For_An_Install()
    {
        // The connector's own scratch space lives under the managed root and the
        // server can write there. If discovery treated it as an install, a server
        // could manufacture a connector inside a directory it already owns.
        var workspaces = Path.Combine(_connectorRoot, "workspaces", "invented");
        Directory.CreateDirectory(workspaces);
        File.WriteAllText(Path.Combine(workspaces, "server.exe"), "binary");
        File.WriteAllText(
            Path.Combine(_connectorRoot, "workspaces",
                ConnectorManifestReader.ManifestFileName),
            Manifest("workspaces").ToJsonString());

        _resolver.Discover().Should().NotContain(item => item.ConnectorId == "workspaces");
    }

    // ── Identity ────────────────────────────────────────────────────────────

    [Fact]
    public async Task Replacing_The_Executable_After_Approval_Breaks_The_Approval()
    {
        Install("swapped");
        await ApproveAsync("swapped");
        _resolver.Resolve("swapped").Resolved.Should().BeTrue();

        await File.WriteAllTextAsync(
            Path.Combine(_connectorRoot, "swapped", "server.exe"), "different binary");

        _resolver.Resolve("swapped").DenialCode.Should().Be("ConnectorExecutableChanged");
    }

    [Fact]
    public async Task Claiming_A_Different_Publisher_After_Approval_Breaks_It()
    {
        Install("relabelled");
        await ApproveAsync("relabelled");

        Install("relabelled", manifest => manifest["publisher"] = "Definitely Microsoft");

        _resolver.Resolve("relabelled").DenialCode.Should().Be("ConnectorPublisherChanged");
    }

    [Fact]
    public async Task Claiming_A_Different_Install_Source_After_Approval_Breaks_It()
    {
        Install("resourced");
        await ApproveAsync("resourced");

        Install("resourced", manifest => manifest["install_source"] = "official-store");

        _resolver.Resolve("resourced").DenialCode.Should().Be("ConnectorInstallSourceChanged");
    }

    [Fact]
    public async Task Asking_For_More_Than_Was_Approved_Requires_A_New_Decision()
    {
        Install("greedy");
        await ApproveAsync("greedy");

        Install("greedy", manifest =>
        {
            manifest["requires_network"] = true;
            manifest["credentials"] = new JsonArray("github-token");
            manifest["workspace_access"] = new JsonArray("workspace", @"C:\Users");
        });

        var resolution = _resolver.Resolve("greedy");
        resolution.DenialCode.Should().Be("ConnectorPermissionExpanded");
        resolution.PermissionExpansions.Should().Contain("network");
        resolution.PermissionExpansions.Should().Contain(@"workspace:C:\Users");
        resolution.PermissionExpansions.Should().Contain("credential:github-token");
    }

    [Fact]
    public async Task Even_Narrowing_The_Declaration_Requires_A_New_Decision()
    {
        Install("modest", manifest => manifest["requires_network"] = true);
        await ApproveAsync("modest");

        Install("modest", manifest => manifest["requires_network"] = false);
        var resolution = _resolver.Resolve("modest");

        // Asking for less is not an escalation, so it is not reported as one —
        // but the approval is still tied to the declaration as a whole. It has
        // to be: the dangerous edits are the ones that look like narrowing, such
        // as moving a delete tool from "destructive" into "read-only", which
        // requests no new permission at all while changing what the runtime will
        // let the connector do after a lost answer.
        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorManifestChanged");
        resolution.PermissionExpansions.Should().BeEmpty();
    }

    [Fact]
    public async Task Reclassifying_A_Destructive_Tool_As_Read_Only_Does_Not_Slip_Through()
    {
        Install("reclassifier", manifest =>
        {
            manifest["tools"] = new JsonArray("ping", "delete_everything");
            manifest["destructive_tools"] = new JsonArray("delete_everything");
        });
        await ApproveAsync("reclassifier");

        Install("reclassifier", manifest =>
        {
            manifest["tools"] = new JsonArray("ping", "delete_everything");
            manifest["read_only_tools"] = new JsonArray("ping", "delete_everything");
            manifest["destructive_tools"] = new JsonArray();
        });

        _resolver.Resolve("reclassifier").Resolved.Should().BeFalse(
            "the risk classification is part of what was approved, and moving a delete "
            + "into read-only asks for no new permission while changing everything about "
            + "what a lost answer permits");
    }

    // ── Legacy and self-service ─────────────────────────────────────────────

    [Fact]
    public async Task A_Legacy_Record_Without_An_Identity_Is_Never_Auto_Enabled()
    {
        Install("legacy");
        var declaration = _resolver.ReadDeclaration("legacy")!;
        await _administration.MigrateLegacyAsync(
            declaration, "Installed before executable identity was recorded.");

        var record = _trust.Get("legacy")!;
        record.State.Should().Be(ConnectorTrustState.AwaitingTrust);
        record.ExecutableSha256.Should().BeEmpty(
            "hashing whatever is on disk now would approve whatever is on disk now");
        record.Enabled.Should().BeFalse();
        _resolver.Resolve("legacy").DenialCode.Should().Be("ConnectorAwaitingTrust");
    }

    [Fact]
    public async Task A_Migrated_Legacy_Record_Becomes_Runnable_Only_After_A_Person_Approves()
    {
        Install("legacy");
        await _administration.MigrateLegacyAsync(
            _resolver.ReadDeclaration("legacy")!, "legacy install");
        _resolver.Resolve("legacy").Resolved.Should().BeFalse();

        await ApproveAsync("legacy");

        _resolver.Resolve("legacy").Resolved.Should().BeTrue();
        _trust.Get("legacy")!.ExecutableSha256.Should().NotBeEmpty();
    }

    [Fact]
    public async Task A_Connector_Rewriting_The_Trust_File_Gains_Nothing_It_Can_Reach()
    {
        Install("selfserve");
        await ApproveAsync("selfserve");

        // The file the connector would have to reach is not under the root its
        // sandbox mounts. This is the property, stated as a location rather than
        // as a promise about what the server will not try.
        Path.GetFullPath(_trustFile).Should().NotStartWith(
            Path.GetFullPath(_connectorRoot));
    }

    [Fact]
    public void An_Unreadable_Trust_Store_Means_Nothing_Is_Approved()
    {
        File.WriteAllText(_trustFile, "{{{ not json");
        var store = new JsonConnectorTrustStore(_trustFile, NullLogger.Instance);

        store.All().Should().BeEmpty(
            "a corrupt approvals file must read as 'nothing is approved', never as "
            + "'no restrictions'");
    }

    [Fact]
    public async Task An_Approval_Cannot_Be_Taken_While_The_Agent_Is_Driving_The_Screen()
    {
        Install("agent-clicked");

        using (AgentInputScope.Enter("computer_click on the approval dialog"))
        {
            var mint = () => UserApprovalEvidence.FromUserGesture("user:desktop", "Allow button");
            mint.Should().Throw<UnauthorizedAccessException>()
                .WithMessage("*driving the user interface*");
        }

        _trust.Get("agent-clicked").Should().BeNull();
        await Task.CompletedTask;
    }

    [Fact]
    public void A_Click_Just_After_Synthetic_Input_Is_Still_Refused()
    {
        // SendInput returns before the click lands, so a scope that closed at the
        // P/Invoke boundary would already be shut when the button was pressed.
        using (AgentInputScope.Enter("uia_click"))
        {
        }

        var mint = () => UserApprovalEvidence.FromUserGesture("user:desktop", "Allow button");

        mint.Should().Throw<UnauthorizedAccessException>();
    }

    [Fact]
    public async Task Approving_A_Connector_Outside_The_Managed_Location_Is_Refused()
    {
        var outside = Path.Combine(_root, "elsewhere");
        Directory.CreateDirectory(outside);
        File.WriteAllText(Path.Combine(outside, "server.exe"), "binary");

        // Point a resolver at the loose directory to obtain a declaration for it,
        // then try to approve it under the real installation policy.
        var looseResolver = new ConnectorManifestResolver(
            outside, _trust, new ConnectorInstallationPolicy([outside]),
            logger: NullLogger.Instance);
        var directory = Path.Combine(outside, "loose");
        Directory.CreateDirectory(directory);
        File.WriteAllText(Path.Combine(directory, "server.exe"), "binary");
        File.WriteAllText(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            Manifest("loose").ToJsonString());

        var administration = new ConnectorTrustAdministration(
            looseResolver, _trust, _installation, NullLogger.Instance);
        var approve = () => administration.ApproveAsync(
            "loose",
            new ConnectorApprovalDecision(ConnectorApprovalOutcome.Approved, "user:test"),
            UserApprovalEvidence.FromUserGesture("user:test", "dialog"));

        await approve.Should().ThrowAsync<InvalidOperationException>()
            .WithMessage("*managed installation location*");
    }

    [Fact]
    public async Task A_Test_Grant_Cannot_Be_Used_To_Run_A_Local_Executable()
    {
        Install("local-test");
        var grants = new TransientConnectorGrants();
        var declaration = _resolver.ReadDeclaration("local-test")!;

        var grant = () => grants.Grant(
            declaration, UserApprovalEvidence.FromUserGesture("user:test", "Test connection"));

        grant.Should().Throw<InvalidOperationException>()
            .WithMessage("*running the executable IS the risk*");
        await Task.CompletedTask;
    }

    [Fact]
    public void A_Test_Grant_Disappears_When_Its_Handle_Is_Released()
    {
        var grants = new TransientConnectorGrants();
        var declaration = new ConnectorDeclaration
        {
            ConnectorId = "remote-test",
            Name = "Remote",
            Version = "1",
            Transport = ConnectorTransportKind.Http,
            ServerUrl = "https://example.test/mcp"
        };

        using (grants.Grant(
                   declaration,
                   UserApprovalEvidence.FromUserGesture("user:test", "Test connection")))
            grants.TryResolve("remote-test").Should().NotBeNull();

        grants.TryResolve("remote-test").Should().BeNull(
            "a pre-install test must not leave an approval behind it");
    }

    // ── Fixtures ────────────────────────────────────────────────────────────

    private async Task ApproveAsync(string connectorId) =>
        await _administration.ApproveAsync(
            connectorId,
            new ConnectorApprovalDecision(ConnectorApprovalOutcome.Approved, "user:test"),
            UserApprovalEvidence.FromUserGesture("user:test", "approval dialog"));

    private void Install(string connectorId, Action<JsonObject>? mutate = null)
    {
        var directory = Path.Combine(_connectorRoot, connectorId);
        Directory.CreateDirectory(directory);
        var executable = Path.Combine(directory, "server.exe");
        if (!File.Exists(executable))
            File.WriteAllText(executable, $"binary for {connectorId}");
        var manifest = Manifest(connectorId);
        mutate?.Invoke(manifest);
        File.WriteAllText(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            manifest.ToJsonString());
    }

    private static JsonObject Manifest(string connectorId) => new()
    {
        ["connector_id"] = connectorId,
        ["name"] = connectorId,
        ["version"] = "1.0.0",
        ["publisher"] = "SRAN Test Fixtures",
        ["transport"] = "stdio",
        ["executable"] = "server.exe",
        ["install_source"] = "local-package",
        ["protocol_version"] = "2025-06-18",
        ["capabilities"] = new JsonArray("tools"),
        ["workspace_access"] = new JsonArray("workspace"),
        ["requires_network"] = false,
        ["credentials"] = new JsonArray(),
        ["tools"] = new JsonArray("ping"),
        ["read_only_tools"] = new JsonArray("ping")
    };

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_root))
                Directory.Delete(_root, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
    }
}
