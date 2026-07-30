using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Phase 4.1C: a real local connector, installed as a package, approved by a
/// person, launched isolated — all through the composition
/// <see cref="SranDesktop.App.Hosting.AppHostBuilder"/> actually builds.
///
/// The distinction being drawn here is the whole point of the phase. Phase 4.1B
/// proved a durable MCP path could be assembled and driven; what it could not
/// prove was that the application used it, and in fact the application's
/// manifest resolver returned null for every id, so nothing ever ran. Every test
/// in this file goes through <see cref="ProductionConnectorHost"/> for that
/// reason: a green suite over a hand-built graph is exactly what hid the gap.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class ProductionConnectorActivationTests : IAsyncLifetime
{
    private const string ConnectorId = "fixture-local";

    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-mcp-prod-{Guid.NewGuid():N}");
    private ProductionConnectorHost _host = null!;

    public Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        AgentInputScope.ResetForTests();
        _host = new ProductionConnectorHost(_root);
        return Task.CompletedTask;
    }

    // ── Activation ──────────────────────────────────────────────────────────

    [Fact]
    public void The_Production_Resolver_Finds_An_Installed_Package()
    {
        ConnectorPackage.Install(_host.ConnectorRoot, ConnectorId);

        _host.Resolver.Discover().Should().ContainSingle(
            item => item.ConnectorId == ConnectorId,
            "a resolver that cannot see an installed connector can never approve one");
        _host.Resolver.ReadDeclaration(ConnectorId)!.Permissions.RequiresNetwork
            .Should().BeFalse();
    }

    [Fact]
    public void An_Installed_But_Unapproved_Connector_Fails_Closed()
    {
        ConnectorPackage.Install(_host.ConnectorRoot, ConnectorId);

        var resolution = _host.Resolver.Resolve(ConnectorId);

        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorUntrusted");
        resolution.Declaration.Should().NotBeNull(
            "the approval screen has to be able to show what is being asked for");
    }

    [Fact]
    public async Task An_Unapproved_Connector_Is_Refused_And_Still_Leaves_A_Step()
    {
        ConnectorPackage.Install(_host.ConnectorRoot, ConnectorId);

        var result = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test");

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorUntrusted");
        result.ActionId.Should().NotBeNullOrEmpty();
        (await _host.Tasks.GetActionAsync(result.ActionId!)).Should().NotBeNull(
            "a refusal is a durable fact, not a silent no-op");
    }

    [Fact]
    public async Task An_Approved_Connector_Runs_The_Whole_Protocol_Through_The_Gateway()
    {
        await ApproveAsync();

        var initialize = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.Initialize), "test");
        var tools = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test");
        var call = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsCall, tool: "ping"), "test");
        var resources = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ResourcesList), "test");
        var read = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ResourcesRead, resource: "fixture://readme"), "test");
        var prompts = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.PromptsList), "test");
        var prompt = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.PromptsGet, promptName: "summarize"), "test");

        var all = new[] { initialize, tools, call, resources, read, prompts, prompt };
        all.Should().OnlyContain(item => item.Status == McpOperationStatus.Succeeded,
            string.Join(" | ", all.Select(item => $"{item.Operation}:{item.FailureReason}")));

        foreach (var result in all)
        {
            result.ActionId.Should().NotBeNullOrEmpty(
                $"{result.Operation} must leave a step behind it");
            (await _host.Tasks.GetActionAsync(result.ActionId!)).Should().NotBeNull();
        }
        all.Select(item => item.ActionId).Distinct().Should().HaveCount(all.Length);
        read.Content.Should().StartWith(UntrustedObservationBoundary.Marker,
            "everything a connector returns is data, never instruction");
    }

    [Fact]
    public async Task The_Approved_Policy_And_Not_The_Server_Decides_What_Is_Read_Only()
    {
        await ApproveAsync();

        var read = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsCall, tool: "get_record",
                arguments: """{"id":"absent"}"""), "test");
        var mutation = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsCall, tool: "create_record",
                arguments: """{"title":"from-policy"}"""), "test");

        read.Risk.Should().Be(McpRiskClass.ReadOnly,
            "the approved manifest lists get_record as read-only");
        mutation.Risk.Should().Be(McpRiskClass.ExternalMutation,
            "and create_record as an externally visible mutation");
    }

    // ── Trust invalidation ──────────────────────────────────────────────────

    [Fact]
    public async Task Replacing_The_Executable_After_Approval_Stops_It_Running()
    {
        await ApproveAsync();
        (await _host.Gateway.ExecuteOwnedAsync(Request(McpOperationKind.ToolsList), "test"))
            .Status.Should().Be(McpOperationStatus.Succeeded);

        await File.AppendAllTextAsync(
            ConnectorPackage.ExecutablePath(_host.ConnectorRoot, ConnectorId), "\0tampered");

        var resolution = _host.Resolver.Resolve(ConnectorId);
        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorExecutableChanged");

        var result = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test");
        result.Status.Should().Be(McpOperationStatus.Blocked);
    }

    [Fact]
    public async Task A_Connector_That_Starts_Asking_For_The_Network_Needs_A_New_Decision()
    {
        await ApproveAsync();

        ConnectorPackage.RewriteManifest(_host.ConnectorRoot, ConnectorId,
            builder => builder.RequiresNetwork = true);

        var resolution = _host.Resolver.Resolve(ConnectorId);

        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorPermissionExpanded");
        resolution.PermissionExpansions.Should().Contain("network");
    }

    [Fact]
    public async Task A_New_Version_Of_The_Same_Connector_Is_Not_Covered_By_The_Old_Approval()
    {
        await ApproveAsync();

        ConnectorPackage.RewriteManifest(_host.ConnectorRoot, ConnectorId,
            builder => builder.Version = "2.0.0");

        var resolution = _host.Resolver.Resolve(ConnectorId);

        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorVersionChanged");
    }

    [Fact]
    public async Task A_Different_Publisher_On_The_Same_Package_Invalidates_It()
    {
        await ApproveAsync();

        ConnectorPackage.RewriteManifest(_host.ConnectorRoot, ConnectorId,
            builder => builder.Publisher = "Somebody Else");

        _host.Resolver.Resolve(ConnectorId).DenialCode.Should().Be("ConnectorPublisherChanged");
    }

    [Fact]
    public async Task Revoking_Trust_Stops_The_Session_That_Is_Already_Running()
    {
        await ApproveAsync();
        (await _host.Gateway.ExecuteOwnedAsync(Request(McpOperationKind.ToolsList), "test"))
            .Status.Should().Be(McpOperationStatus.Succeeded);

        await _host.Trust.RevokeAsync(ConnectorId, Evidence());

        var refused = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test");
        refused.Status.Should().Be(McpOperationStatus.Blocked);
        refused.FailureCode.Should().Be("ConnectorRevoked");

        // The teardown is asynchronous by design — a revocation must not block on
        // a server that is mid-write — so give it a moment before asserting the
        // process is actually gone.
        await WaitForNoSurvivingServerAsync();
    }

    [Fact]
    public async Task Disabling_A_Connector_Survives_A_Restart()
    {
        await ApproveAsync();
        await _host.Trust.DisableAsync(ConnectorId, Evidence());
        await _host.DisposeAsync();

        _host = new ProductionConnectorHost(_root);

        var resolution = _host.Resolver.Resolve(ConnectorId);
        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorDisabled");
    }

    [Fact]
    public async Task Reapproving_The_Same_Package_Brings_It_Back()
    {
        await ApproveAsync();
        await _host.Trust.RevokeAsync(ConnectorId, Evidence());
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeFalse();

        await ApproveAsync();

        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();
        (await _host.Gateway.ExecuteOwnedAsync(Request(McpOperationKind.ToolsList), "test"))
            .Status.Should().Be(McpOperationStatus.Succeeded);
    }

    [Fact]
    public async Task Reapproving_After_The_Binary_Changed_Approves_The_NEW_Binary()
    {
        await ApproveAsync();
        var before = _host.TrustStore.Get(ConnectorId)!.ExecutableSha256;
        await File.AppendAllTextAsync(
            ConnectorPackage.ExecutablePath(_host.ConnectorRoot, ConnectorId), "\0v2");

        await ApproveAsync();

        var after = _host.TrustStore.Get(ConnectorId)!.ExecutableSha256;
        after.Should().NotBe(before,
            "re-approval records what is on disk NOW, not what was approved before");
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();
    }

    // ── Trust storage ───────────────────────────────────────────────────────

    [Fact]
    public async Task The_Trust_Record_Survives_A_Restart_And_Names_What_Was_Approved()
    {
        var declaration = await ApproveAsync();
        await _host.DisposeAsync();

        _host = new ProductionConnectorHost(_root);
        var record = _host.TrustStore.Get(ConnectorId);

        record.Should().NotBeNull();
        record!.State.Should().Be(ConnectorTrustState.Trusted);
        record.Enabled.Should().BeTrue();
        record.Version.Should().Be(declaration.Version);
        record.ExecutableSha256.Should().NotBeNullOrEmpty();
        record.ManifestFingerprint.Should().NotBeNullOrEmpty();
        record.ApprovedBy.Should().Be("user:test");
        record.ApprovedPermissions.WorkspaceAccess.Should().Contain("connector-workspace");
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();
    }

    [Fact]
    public async Task The_Trust_File_Lives_Outside_Anything_A_Connector_Can_Reach()
    {
        await ApproveAsync();

        var trustFile = Path.Combine(_host.DataDirectory, "connector-trust.json");
        File.Exists(trustFile).Should().BeTrue();
        Path.GetFullPath(trustFile).Should().NotStartWith(
            Path.GetFullPath(_host.ConnectorRoot),
            "a connector that can rewrite its own approval needs one launch to grant "
            + "itself everything");
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private static McpOperationRequest Request(
        McpOperationKind operation,
        string? tool = null,
        string? resource = null,
        string? promptName = null,
        string arguments = "{}") => new()
    {
        ConnectorId = ConnectorId,
        Operation = operation,
        ToolName = tool,
        ResourceUri = resource,
        PromptName = promptName,
        ArgumentsJson = arguments
    };

    private static UserApprovalEvidence Evidence() =>
        UserApprovalEvidence.FromUserGesture("user:test", "approval dialog");

    private async Task<ConnectorDeclaration> ApproveAsync()
    {
        var declaration = _host.Resolver.ReadDeclaration(ConnectorId)
                          ?? ConnectorPackage.Install(_host.ConnectorRoot, ConnectorId);
        await _host.Trust.ApproveAsync(
            ConnectorId,
            new ConnectorApprovalDecision(ConnectorApprovalOutcome.Approved, "user:test"),
            Evidence());
        return _host.Resolver.ReadDeclaration(ConnectorId)!;
    }

    private async Task WaitForNoSurvivingServerAsync()
    {
        for (var attempt = 0; attempt < 40; attempt++)
        {
            if (!SurvivingServers().Any())
                return;
            await Task.Delay(100);
        }
        SurvivingServers().Should().BeEmpty(
            "a revoked connector must not still be running");
    }

    private IEnumerable<System.Diagnostics.Process> SurvivingServers() =>
        System.Diagnostics.Process.GetProcessesByName("SranDesktop.McpFixtureServer")
            .Where(process => !process.HasExited);

    public async Task DisposeAsync()
    {
        await _host.DisposeAsync();
        await ProductionConnectorHost.DeletePatientlyAsync(_root);
    }
}
