using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Connectors;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Install, update, rollback, disable, revoke, uninstall — over the production
/// composition, with a real connector package and a real MCP server.
///
/// The recurring assertion is that an approval never survives a change to the
/// thing it was about. An update installs but does not run; a rollback restores
/// the files but not the decision; an uninstall leaves an audit record rather
/// than a clean slate a reinstall could inherit.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class ConnectorLifecycleTests : IAsyncLifetime
{
    private const string ConnectorId = "fixture-lifecycle";

    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-lifecycle-{Guid.NewGuid():N}");
    private string _packageSource = "";
    private ProductionConnectorHost _host = null!;
    private ConnectorPackageInstaller _installer = null!;

    public Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        AgentInputScope.ResetForTests();
        _host = new ProductionConnectorHost(_root);
        _installer = NewInstaller();

        // A package staged the way a download would arrive: outside the managed
        // location, complete, and not yet trusted by anything.
        _packageSource = Path.Combine(_root, "incoming", ConnectorId);
        StagePackage(_packageSource);
        return Task.CompletedTask;
    }

    private ConnectorPackageInstaller NewInstaller() => new(
        _host.ConnectorRoot,
        _host.TrustStore,
        _host.Services.GetService(typeof(IConnectorCredentialStore))
            as IConnectorCredentialStore,
        connectorId => _host.Services.GetService(
                typeof(SranDesktop.Infrastructure.Mcp.Remote.IMcpConnectorSessionProvider))
            is SranDesktop.Infrastructure.Mcp.Remote.IMcpConnectorSessionProvider sessions
            ? sessions.ReleaseAsync(connectorId)
            : Task.CompletedTask);

    // ── Install ─────────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Clean_Install_Lands_Unapproved()
    {
        var result = await _installer.InstallAsync(ConnectorId, _packageSource);

        result.Outcome.Should().Be(ConnectorInstallOutcome.Installed);
        result.RequiresApproval.Should().BeTrue();
        _host.Resolver.Discover().Should().ContainSingle(item => item.ConnectorId == ConnectorId);
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeFalse(
            "installing is not deciding");
        _host.TrustStore.Get(ConnectorId)!.State.Should().Be(ConnectorTrustState.AwaitingTrust);
    }

    [Fact]
    public async Task An_Installed_And_Approved_Connector_Runs()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();

        var result = await _host.Gateway.ExecuteOwnedAsync(
            Call("ping"), "test:lifecycle");

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
    }

    [Fact]
    public async Task A_Package_Whose_Entry_Escapes_The_Install_Directory_Is_Refused()
    {
        var hostile = Path.Combine(_root, "hostile");
        Directory.CreateDirectory(Path.Combine(hostile, "nested"));
        File.WriteAllText(Path.Combine(hostile, "nested", "payload.txt"), "x");
        File.WriteAllText(
            Path.Combine(hostile, ConnectorManifestReader.ManifestFileName),
            ManifestJson(executable: @"..\..\escaped.exe"));

        var result = await _installer.InstallAsync(ConnectorId, hostile);

        result.Succeeded.Should().BeFalse();
        Directory.Exists(Path.Combine(_host.ConnectorRoot, ConnectorId)).Should().BeFalse(
            "a package that fails validation must not occupy the connector's name");
    }

    [Fact]
    public async Task An_Invalid_Manifest_Leaves_A_Working_Install_Untouched()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        var goodHash = _host.TrustStore.Get(ConnectorId)!.ExecutableSha256;

        var broken = Path.Combine(_root, "broken");
        Directory.CreateDirectory(broken);
        File.WriteAllText(Path.Combine(broken, "server.exe"), "x");
        File.WriteAllText(
            Path.Combine(broken, ConnectorManifestReader.ManifestFileName), "{ not json");
        var result = await _installer.InstallAsync(ConnectorId, broken);

        result.Succeeded.Should().BeFalse();
        _host.TrustStore.Get(ConnectorId)!.ExecutableSha256.Should().Be(goodHash);
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue(
            "the connector that was working still is");
    }

    // ── Update ──────────────────────────────────────────────────────────────

    [Fact]
    public async Task An_Update_Installs_But_Does_Not_Inherit_The_Approval()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();

        var next = Path.Combine(_root, "incoming-v2");
        StagePackage(next, version: "2.0.0");
        var result = await _installer.InstallAsync(ConnectorId, next);

        result.Outcome.Should().Be(ConnectorInstallOutcome.Updated);
        result.RequiresApproval.Should().BeTrue();
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeFalse(
            "a new binary is a new thing, whatever its version number says");
        _host.Resolver.ReadDeclaration(ConnectorId)!.Version.Should().Be("2.0.0");
    }

    [Fact]
    public async Task A_Permission_Expanding_Update_Says_What_It_Now_Wants()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();

        var next = Path.Combine(_root, "incoming-greedy");
        StagePackage(next, version: "2.0.0", requiresNetwork: true);
        var result = await _installer.InstallAsync(ConnectorId, next);

        result.PermissionExpansions.Should().Contain("network");
        _host.TrustStore.Get(ConnectorId)!.StateReason.Should().Contain("network");
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeFalse();
    }

    [Fact]
    public async Task An_Update_Can_Be_Approved_And_Then_Runs()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        var next = Path.Combine(_root, "incoming-v2");
        StagePackage(next, version: "2.0.0");
        await _installer.InstallAsync(ConnectorId, next);

        await ApproveAsync();

        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();
        (await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "test:lifecycle"))
            .Status.Should().Be(McpOperationStatus.Succeeded);
    }

    [Fact]
    public async Task An_Update_Stops_The_Session_The_Old_Version_Was_Running()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        (await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "test:lifecycle"))
            .Status.Should().Be(McpOperationStatus.Succeeded);

        var next = Path.Combine(_root, "incoming-v2");
        StagePackage(next, version: "2.0.0");
        var result = await _installer.InstallAsync(ConnectorId, next);

        result.Succeeded.Should().BeTrue(result.FailureReason);
        Directory.Exists(Path.Combine(_host.ConnectorRoot, ConnectorId)).Should().BeTrue(
            "the switch could not have completed with a live process holding the files");
    }

    // ── Rollback ────────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Rollback_Restores_The_Previous_Version_Unapproved()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        var next = Path.Combine(_root, "incoming-v2");
        StagePackage(next, version: "2.0.0");
        await _installer.InstallAsync(ConnectorId, next);
        _installer.HasPreviousVersion(ConnectorId).Should().BeTrue();

        var result = await _installer.RollbackAsync(ConnectorId);

        result.Outcome.Should().Be(ConnectorInstallOutcome.RolledBack);
        _host.Resolver.ReadDeclaration(ConnectorId)!.Version.Should().Be("1.0.0");
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeFalse(
            "restoring files is not restoring a decision nobody re-made");
    }

    [Fact]
    public async Task A_Rolled_Back_Version_Runs_Once_It_Is_Approved_Again()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        var next = Path.Combine(_root, "incoming-v2");
        StagePackage(next, version: "2.0.0");
        await _installer.InstallAsync(ConnectorId, next);
        await _installer.RollbackAsync(ConnectorId);

        await ApproveAsync();

        (await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "test:lifecycle"))
            .Status.Should().Be(McpOperationStatus.Succeeded);
    }

    [Fact]
    public async Task There_Is_Nothing_To_Roll_Back_To_After_A_First_Install()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);

        var result = await _installer.RollbackAsync(ConnectorId);

        result.Succeeded.Should().BeFalse();
        result.FailureCode.Should().Be("NoPreviousVersion");
    }

    // ── Crash points ────────────────────────────────────────────────────────

    [Fact]
    public async Task Staging_Left_By_A_Crashed_Install_Is_Not_A_Connector()
    {
        // A process that died between "copy" and "switch" leaves this behind.
        var staging = Path.Combine(_host.ConnectorRoot, ConnectorId + ".staging");
        StagePackage(staging, version: "9.9.9");

        _host.Resolver.Discover().Should().NotContain(item =>
            item.ConnectorId.Contains("staging", StringComparison.Ordinal));

        _installer.CleanStaging();
        Directory.Exists(staging).Should().BeFalse();
    }

    [Fact]
    public async Task A_Previous_Version_Directory_Is_Not_Discoverable_As_A_Connector()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        var next = Path.Combine(_root, "incoming-v2");
        StagePackage(next, version: "2.0.0");
        await _installer.InstallAsync(ConnectorId, next);

        // The kept copy must not be resolvable — its manifest declares the same
        // connector id, and two live entries for one id is exactly the ambiguity
        // the id check exists to prevent.
        _host.Resolver.Discover()
            .Count(item => item.ConnectorId == ConnectorId)
            .Should().Be(1);
    }

    // ── Disable / revoke / uninstall ────────────────────────────────────────

    [Fact]
    public async Task A_Disabled_Connector_Cannot_Run_And_Stays_Disabled_After_Restart()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        await _host.Trust.DisableAsync(ConnectorId, Evidence());

        await _host.DisposeAsync();
        _host = new ProductionConnectorHost(_root);

        _host.Resolver.Resolve(ConnectorId).DenialCode.Should().Be("ConnectorDisabled");
    }

    [Fact]
    public async Task Uninstall_Removes_The_Files_And_Leaves_An_Audit_Record()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        var approvedHash = _host.TrustStore.Get(ConnectorId)!.ExecutableSha256;

        var result = await _installer.UninstallAsync(ConnectorId, removeCredentials: true);

        result.Outcome.Should().Be(ConnectorInstallOutcome.Uninstalled);
        Directory.Exists(Path.Combine(_host.ConnectorRoot, ConnectorId)).Should().BeFalse();
        var record = _host.TrustStore.Get(ConnectorId);
        record.Should().NotBeNull("an uninstall that erases its own history cannot be audited");
        record!.State.Should().Be(ConnectorTrustState.Revoked);
        record.ExecutableSha256.Should().Be(approvedHash,
            "the audit has to be able to say WHAT was trusted");
    }

    [Fact]
    public async Task Reinstalling_After_Uninstall_Does_Not_Inherit_The_Old_Approval()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        await _installer.UninstallAsync(ConnectorId, removeCredentials: true);

        await _installer.InstallAsync(ConnectorId, _packageSource);

        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeFalse(
            "the same id is not the same decision");
        _host.TrustStore.Get(ConnectorId)!.State.Should().Be(ConnectorTrustState.AwaitingTrust);
    }

    [Fact]
    public async Task Uninstall_Removes_The_Connector_Workspace()
    {
        await _installer.InstallAsync(ConnectorId, _packageSource);
        await ApproveAsync();
        await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "test:lifecycle");
        var workspace = Path.Combine(_host.ConnectorRoot, "workspaces", ConnectorId);

        await _installer.UninstallAsync(ConnectorId, removeCredentials: true);

        Directory.Exists(workspace).Should().BeFalse(
            "a removed connector's scratch space goes with it");
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private static McpOperationRequest Call(string tool) => new()
    {
        ConnectorId = ConnectorId,
        Operation = McpOperationKind.ToolsCall,
        ToolName = tool,
        ArgumentsJson = "{}"
    };

    private static UserApprovalEvidence Evidence() =>
        UserApprovalEvidence.FromUserGesture("user:test", "approval dialog");

    private async Task ApproveAsync() =>
        await _host.Trust.ApproveAsync(
            ConnectorId,
            new ConnectorApprovalDecision(ConnectorApprovalOutcome.Approved, "user:test"),
            Evidence());

    private static void StagePackage(
        string directory, string version = "1.0.0", bool requiresNetwork = false)
    {
        Directory.CreateDirectory(directory);
        foreach (var file in Directory.GetFiles(
                     ConnectorPackage.SourceImage, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(ConnectorPackage.SourceImage, file);
            var target = Path.Combine(directory, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
        File.WriteAllText(
            Path.Combine(directory, ConnectorManifestReader.ManifestFileName),
            ManifestJson(version: version, requiresNetwork: requiresNetwork));
    }

    private static string ManifestJson(
        string version = "1.0.0",
        bool requiresNetwork = false,
        string? executable = null)
    {
        var builder = new ConnectorPackage.ManifestBuilder(
            ConnectorId, executable ?? ConnectorPackage.ServerFileName)
        {
            Version = version,
            RequiresNetwork = requiresNetwork
        };
        return builder.ToJson();
    }

    public async Task DisposeAsync()
    {
        await _host.DisposeAsync();
        await ProductionConnectorHost.DeletePatientlyAsync(_root);
    }
}
