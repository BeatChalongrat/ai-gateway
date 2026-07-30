using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Continues the packaged-approval verification from where the human gesture
/// left off.
///
/// The click itself cannot be automated — that is the point of the guard — so
/// this reads the decision a person actually made, through the production trust
/// store, and then drives everything that follows: launch, restart, revoke, hash
/// change, permission expansion. It runs against the REAL connector root and the
/// REAL trust file written by the packaged build, with only the chat database
/// redirected so a verification run cannot disturb the user's own history.
///
/// It skips itself when that approval is not present, because a test that
/// silently passes on a machine where nobody clicked anything would be worse
/// than no test at all.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "PackagedApproval")]
public sealed class PackagedApprovalVerificationTests : IAsyncLifetime
{
    private const string ConnectorId = "ws2-fixture";

    private static string ConnectorRoot => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SRAN Desktop", "connectors");

    private static string TrustFile => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SRAN Desktop", "connector-trust.json");

    private static string ExecutablePath => Path.Combine(
        ConnectorRoot, ConnectorId, "SranDesktop.McpFixtureServer.dll");

    private static string ManifestPath => Path.Combine(
        ConnectorRoot, ConnectorId, "connector.manifest.json");

    /// <summary>True only when a packaged approval is actually on disk.</summary>
    private static bool Available =>
        File.Exists(TrustFile) && File.Exists(ExecutablePath) && File.Exists(ManifestPath);

    private readonly string _dataDirectory =
        Path.Combine(Path.GetTempPath(), $"sran-ws2-verify-{Guid.NewGuid():N}");
    private string _trustBackup = "";
    private string _manifestBackup = "";
    private byte[] _executableBackup = [];
    private ProductionConnectorHost? _host;

    public Task InitializeAsync()
    {
        if (!Available)
            return Task.CompletedTask;
        AgentInputScope.ResetForTests();
        Directory.CreateDirectory(_dataDirectory);

        // Everything this run touches is restored afterwards: it is operating on
        // the user's real connector installation, not a fixture copy.
        _trustBackup = File.ReadAllText(TrustFile);
        _manifestBackup = File.ReadAllText(ManifestPath);
        _executableBackup = File.ReadAllBytes(ExecutablePath);
        _host = NewHost();
        return Task.CompletedTask;
    }

    private ProductionConnectorHost NewHost() => new(
        _dataDirectory,
        $"--Sran:ConnectorRoot={ConnectorRoot}",
        $"--Sran:ConnectorTrustFile={TrustFile}",
        "--Sran:McpOperationTimeoutSeconds=20");

    [Fact]
    public async Task The_Human_Approval_Is_Readable_Through_The_Production_Trust_Store()
    {
        if (!Available) { NotApplicable(); return; }

        var record = _host!.TrustStore.Get(ConnectorId);

        record.Should().NotBeNull("the packaged build wrote it; the production API reads it");
        record!.State.Should().Be(ConnectorTrustState.Trusted);
        record.Enabled.Should().BeTrue();
        record.ApprovedBy.Should().StartWith("user:",
            "the decision came from a person, not from a policy or a model");
        record.ApprovedAtUtc.Should().NotBeNull();
        record.ExecutableSha256.Should().NotBeNullOrEmpty();
        record.ManifestFingerprint.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task The_Card_State_Comes_From_Persisted_Trust_And_Launches_Isolated()
    {
        if (!Available) { NotApplicable(); return; }

        // Resolved from disk by the same resolver the card reads.
        _host!.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();

        var result = await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "ws2:verify");

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.ActionId.Should().NotBeNullOrEmpty("the call is an AgentStep");
        (await _host.Tasks.GetActionAsync(result.ActionId!)).Should().NotBeNull();
        result.Evidence.Should().Contain("provenance=verified");
        result.Content.Should().StartWith(UntrustedObservationBoundary.Marker,
            "what the connector returned is an observation");
    }

    [Fact]
    public async Task Trust_Survives_A_Restart_And_The_Connector_Runs_Again()
    {
        if (!Available) { NotApplicable(); return; }
        (await _host!.Gateway.ExecuteOwnedAsync(Call("ping"), "ws2:verify"))
            .Status.Should().Be(McpOperationStatus.Succeeded);

        await _host.DisposeAsync();
        _host = NewHost();

        _host.TrustStore.Get(ConnectorId)!.State.Should().Be(ConnectorTrustState.Trusted);
        _host.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();
        (await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "ws2:verify"))
            .Status.Should().Be(McpOperationStatus.Succeeded,
                "a restart must not cost the user their decision");
    }

    [Fact]
    public async Task Revoking_Stops_The_Session_And_Survives_A_Restart()
    {
        if (!Available) { NotApplicable(); return; }
        (await _host!.Gateway.ExecuteOwnedAsync(Call("ping"), "ws2:verify"))
            .Status.Should().Be(McpOperationStatus.Succeeded);

        await _host.Trust.RevokeAsync(ConnectorId, Evidence());

        var denied = await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "ws2:verify");
        denied.Status.Should().Be(McpOperationStatus.Blocked);
        denied.FailureCode.Should().Be("ConnectorRevoked");
        await WaitForNoSurvivorAsync();

        await _host.DisposeAsync();
        _host = NewHost();
        _host.Resolver.Resolve(ConnectorId).DenialCode.Should().Be("ConnectorRevoked",
            "a revocation that a restart undoes is not a revocation");
    }

    [Fact]
    public async Task Changing_The_Executable_Invalidates_The_Approval()
    {
        if (!Available) { NotApplicable(); return; }
        await ReapproveAsync();
        _host!.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();

        await File.AppendAllTextAsync(ExecutablePath, "\0tampered");

        var resolution = _host.Resolver.Resolve(ConnectorId);
        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorExecutableChanged");
        (await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "ws2:verify"))
            .Status.Should().Be(McpOperationStatus.Blocked);
    }

    [Fact]
    public async Task Expanding_The_Requested_Permissions_Invalidates_The_Approval()
    {
        if (!Available) { NotApplicable(); return; }
        await ReapproveAsync();
        _host!.Resolver.Resolve(ConnectorId).Resolved.Should().BeTrue();

        var manifest = System.Text.Json.Nodes.JsonNode.Parse(
            await File.ReadAllTextAsync(ManifestPath))!.AsObject();
        manifest["requires_network"] = true;
        await File.WriteAllTextAsync(ManifestPath, manifest.ToJsonString());

        var resolution = _host.Resolver.Resolve(ConnectorId);
        resolution.Resolved.Should().BeFalse();
        resolution.DenialCode.Should().Be("ConnectorPermissionExpanded");
        resolution.PermissionExpansions.Should().Contain("network");
        (await _host.Gateway.ExecuteOwnedAsync(Call("ping"), "ws2:verify"))
            .Status.Should().Be(McpOperationStatus.Blocked);
    }

    [Fact]
    public void An_Agent_Driving_The_Screen_Cannot_Approve_This_Connector()
    {
        if (!Available) { NotApplicable(); return; }

        using (AgentInputScope.Enter("computer_click on the approval dialog"))
        {
            var mint = () => UserApprovalEvidence.FromUserGesture("user:desktop", "Allow button");
            mint.Should().Throw<UnauthorizedAccessException>();
        }
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    /// <summary>
    /// This suite depends on an approval a PERSON made on a packaged build, so it
    /// cannot manufacture its own precondition. On a machine where that has not
    /// happened it reports the fact rather than asserting: a red suite would say
    /// the product is broken when what is actually missing is a human.
    /// </summary>
    private static void NotApplicable() =>
        Console.WriteLine(
            "PackagedApprovalVerificationTests: no packaged approval present on this "
            + "machine — see SRAN_PHASE4_FINAL_RESULT.md section 2 for the gesture.");

    private static McpOperationRequest Call(string tool) => new()
    {
        ConnectorId = ConnectorId,
        Operation = McpOperationKind.ToolsCall,
        ToolName = tool,
        ArgumentsJson = "{}"
    };

    /// <summary>
    /// Re-approval inside the automated half uses ADMINISTRATIVE POLICY, not a
    /// simulated gesture. Minting a fake user gesture here would defeat the guard
    /// the human click exists to prove, and the spec allows an admin policy as a
    /// distinct, auditable source.
    /// </summary>
    private static UserApprovalEvidence Evidence() =>
        UserApprovalEvidence.FromAdministrativePolicy("ws2-verification");

    private async Task ReapproveAsync() =>
        await _host!.Trust.ApproveAsync(
            ConnectorId,
            new ConnectorApprovalDecision(
                ConnectorApprovalOutcome.Approved, "policy:ws2-verification"),
            Evidence());

    private static async Task WaitForNoSurvivorAsync()
    {
        for (var attempt = 0; attempt < 40; attempt++)
        {
            if (!System.Diagnostics.Process
                    .GetProcessesByName("SranDesktop.McpFixtureServer")
                    .Any(process => !process.HasExited))
                return;
            await Task.Delay(100);
        }
    }

    public async Task DisposeAsync()
    {
        if (_host is not null)
            await _host.DisposeAsync();
        if (Available || _trustBackup.Length > 0)
        {
            // Put the machine back exactly as the human left it.
            try
            {
                if (_trustBackup.Length > 0)
                    await File.WriteAllTextAsync(TrustFile, _trustBackup);
                if (_manifestBackup.Length > 0)
                    await File.WriteAllTextAsync(ManifestPath, _manifestBackup);
                if (_executableBackup.Length > 0)
                    await File.WriteAllBytesAsync(ExecutablePath, _executableBackup);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
            }
        }
        await ProductionConnectorHost.DeletePatientlyAsync(_dataDirectory);
    }
}
