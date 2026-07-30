using FluentAssertions;
using Xunit;

namespace SranDesktop.Tests.Security;

/// <summary>
/// Source-level invariants for MCP.
///
/// Behavioural tests prove that today's operations go through the durable
/// gateway. These prove that TOMORROW's cannot quietly avoid it — a new file
/// that dials a connector directly would pass every behavioural test in the
/// suite while putting a side effect in the world with no AgentStep behind it,
/// which is precisely the class of regression this phase exists to close.
/// </summary>
public sealed class McpRuntimeBypassInvariantTests
{
    /// <summary>Protocol calls that reach a server. Anywhere these appear
    /// outside the allowlist is an operation escaping the runtime.</summary>
    private static readonly string[] ProtocolCalls =
    [
        "CallToolAsync(", "ListToolsAsync(", "ListResourcesAsync(", "ReadResourceAsync(",
        "ListPromptsAsync(", "GetPromptAsync("
    ];

    /// <summary>
    /// Each entry is a file that legitimately speaks the protocol, with the
    /// reason. Adding one is a deliberate decision about where a connector call
    /// may originate.
    /// </summary>
    private static readonly Dictionary<string, string> ProtocolCallAllowlist = new(
        StringComparer.OrdinalIgnoreCase)
    {
        // The protocol client itself.
        ["McpRemoteClient.cs"] = "is the protocol client",
        // The one door: prepares the step, validates trust, records the intent.
        ["McpDurableGateway.cs"] = "is the durable gateway",
        // Reached only from the gateway's reconciliation path.
        ["McpMutationReconciler.cs"] = "reads state back on behalf of the gateway",
        // Performs the handshake that makes a session usable; the gateway
        // records that handshake as its own step.
        ["IsolatedMcpConnectorSessionProvider.cs"] = "owns initialize for a session"

        // McpConnectorService is deliberately NOT here any more. Until Phase
        // 4.1C it dialled marketplace connectors itself, which meant a remote
        // tool call reached the outside world with no AgentStep, no trust check,
        // and no reconciliation. It now holds the catalog, the install records,
        // and the credentials, and every protocol operation goes through the
        // gateway — so putting it back on this list would be re-opening the hole.
    };

    [Fact]
    public void No_Production_File_Speaks_MCP_Outside_The_Durable_Gateway()
    {
        var offenders = ProductionSources()
            .Where(file => !ProtocolCallAllowlist.ContainsKey(Path.GetFileName(file)))
            .Where(file => ProtocolCalls.Any(
                call => File.ReadAllText(file).Contains(call, StringComparison.Ordinal)))
            .Select(Path.GetFileName)
            .ToArray();

        offenders.Should().BeEmpty(
            "an MCP call outside the durable gateway has no AgentStep, no recorded "
            + "dispatch intent, and no reconciliation when its answer is lost");
    }

    [Fact]
    public void The_Gateway_Actually_Uses_The_PrepareAction_RecordResult_Path()
    {
        var source = File.ReadAllText(GatewaySource());

        source.Should().Contain("PrepareActionAsync(",
            "an MCP operation must create the AgentStep before it dispatches");
        source.Should().Contain("RecordResultAsync(",
            "an MCP operation must commit its outcome to the durable run");
        source.Should().Contain("ConnectorTrustPolicy.Evaluate(",
            "trust is validated on every launch, not once at install time");
        source.Should().Contain("_journal.RecordAsync(",
            "the dispatch intent must be persisted before anything is sent");
        source.Should().Contain("ReconcileAsync(",
            "a mutation with an unknown outcome must be reconciled, not retried");
    }

    [Fact]
    public void The_Intent_Is_Recorded_Before_The_Session_Is_Even_Acquired()
    {
        var source = File.ReadAllText(GatewaySource());
        var recorded = source.IndexOf("await _journal.RecordAsync(intent, ct);",
            StringComparison.Ordinal);
        var acquired = source.IndexOf("await _sessions.AcquireAsync(request.ConnectorId, ct)",
            StringComparison.Ordinal);

        recorded.Should().BeGreaterThan(0);
        acquired.Should().BeGreaterThan(0);
        recorded.Should().BeLessThan(
            acquired,
            "a journal written after dispatch would be empty in exactly the case "
            + "it exists for");
    }

    /// <summary>
    /// The handshake is the other way in. A file that builds its own client can
    /// initialize a connector without the trust check, whatever it does next.
    /// </summary>
    [Fact]
    public void Only_The_Session_Owners_Build_A_Protocol_Client()
    {
        var allowed = new[]
        {
            "IsolatedMcpConnectorSessionProvider.cs"
        };

        var offenders = ProductionSources()
            .Where(file => !allowed.Contains(Path.GetFileName(file), StringComparer.OrdinalIgnoreCase))
            .Where(file => File.ReadAllText(file).Contains(
                "new McpRemoteClient(", StringComparison.Ordinal))
            .Select(Path.GetFileName)
            .ToArray();

        offenders.Should().BeEmpty(
            "a file that dials its own client can complete a handshake with a "
            + "connector nobody validated");
    }

    [Fact]
    public void Only_The_Session_Provider_Starts_An_Isolated_Mcp_Server()
    {
        var offenders = ProductionSources()
            .Where(file => Path.GetFileName(file)
                is not "IsolatedMcpConnectorSessionProvider.cs")
            .Where(file => File.ReadAllText(file).Contains(
                "new IsolatedStdioMcpTransport(", StringComparison.Ordinal))
            .Select(Path.GetFileName)
            .ToArray();

        offenders.Should().BeEmpty(
            "a second place that launches a connector is a second place that can "
            + "skip the trust check");
    }

    [Fact]
    public void Local_Stdio_Outside_The_Sandbox_Stays_Fail_Closed()
    {
        var source = File.ReadAllText(Path.Combine(
            RemoteDirectory(), "StdioMcpTransport.cs"));

        source.Should().Contain("Blocked(",
            "the un-sandboxed local transport must remain unusable; it is the "
            + "host-execution fallback this phase must not have");
    }

    [Fact]
    public void Connector_Tools_Carry_No_Protocol_Logic_Of_Their_Own()
    {
        var source = File.ReadAllText(Path.Combine(
            RemoteDirectory(), "LocalMcpConnectorTool.cs"));

        source.Should().Contain("ExecuteAttachedAsync(",
            "a connector tool routes through the gateway rather than dialing");
        source.Should().NotContain("new McpRemoteClient(",
            "a tool that builds its own client has left the runtime");
    }

    /// <summary>
    /// A gateway nobody composes is a gateway nothing goes through. This is the
    /// difference between "the durable path exists" and "the durable path is the
    /// path", which is the whole claim.
    /// </summary>
    [Fact]
    public void The_Application_Actually_Composes_The_Durable_Gateway()
    {
        var source = File.ReadAllText(AppHostBuilderSource());

        source.Should().Contain("IMcpDurableGateway",
            "the gateway must be registered, not merely available");
        source.Should().Contain("McpDispatchJournal(",
            "without a journal the gateway cannot record an intent before dispatch");
        source.Should().Contain("ConnectorInstallationPolicy(",
            "the controlled installation location is a deployment decision, "
            + "not a default the gateway should have to guess");
    }

    /// <summary>
    /// The regression this exists for actually happened. Phase 4.1B shipped a
    /// composed gateway whose manifest resolver was the literal <c>_ => null</c>,
    /// so every connector id came back "not installed" and nothing ever ran —
    /// while the whole MCP suite stayed green, because the tests wired their own
    /// resolver. A gateway with no way to resolve a connector is not a runtime;
    /// it is a very thorough refusal.
    /// </summary>
    [Fact]
    public void The_Application_Composes_A_Resolver_That_Can_Actually_Find_A_Connector()
    {
        var source = File.ReadAllText(AppHostBuilderSource());

        source.Should().Contain("ConnectorManifestResolver(",
            "the production resolver must be the one the gateway asks");
        source.Should().Contain("IConnectorTrustStore",
            "approvals have to be persisted somewhere the connector cannot reach");
        source.Should().Contain("IConnectorTrustAdministration",
            "there must be a composed way for a person to approve a connector");
        source.Should().NotContain("_ => null,",
            "a resolver that answers null for every id means no connector can ever "
            + "run, however complete the runtime behind it looks");
    }

    /// <summary>
    /// The approval has to be reachable from the user interface. A trust
    /// administration nobody can call from a window is the same gap in a
    /// different place.
    /// </summary>
    [Fact]
    public void A_Person_Can_Reach_The_Approval_Decision_From_The_User_Interface()
    {
        var dialog = Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Presentation", "Views", "Dialogs",
            "ConnectorApprovalDialog.xaml.cs");
        File.Exists(dialog).Should().BeTrue("the approval screen has to exist");

        var source = File.ReadAllText(dialog);
        source.Should().Contain("UserApprovalEvidence.FromUserGesture(",
            "the decision must carry proof that a person made it");

        var viewModel = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Presentation", "ViewModels",
            "LocalConnectorItemViewModel.cs"));
        viewModel.Should().Contain("ConnectorApprovalDialog(",
            "and the list of installed connectors has to be able to open it");
        viewModel.Should().Contain("ApproveAsync(",
            "and to record what was decided");
    }

    /// <summary>
    /// The agent can click and it can invoke UI Automation patterns. An approval
    /// dialog is a window with an Allow button. If synthetic input is not marked
    /// at the point it is produced, the model can approve connectors for itself
    /// and every other control in this phase rests on nothing.
    /// </summary>
    [Fact]
    public void Synthetic_Input_Marks_Itself_So_An_Approval_Can_Refuse_It()
    {
        var win32 = File.ReadAllText(Path.Combine(
            RepositoryRoot(), "src", "SranDesktop.Infrastructure", "Native", "Win32Input.cs"));
        win32.Should().Contain("AgentInputScope.Touch(",
            "every synthetic keystroke and mouse event passes through one SendInput "
            + "wrapper; marking it there is what makes the marker complete");

        foreach (var tool in new[] { "UiaClickTool.cs", "UiaSetValueTool.cs" })
            File.ReadAllText(Path.Combine(
                    RepositoryRoot(), "src", "SranDesktop.Infrastructure", "Mcp", "Tools", tool))
                .Should().Contain("AgentInputScope.Enter(",
                    $"{tool} presses buttons without synthetic input, so it has to "
                    + "declare itself separately");

        File.ReadAllText(Path.Combine(
                RepositoryRoot(), "src", "SranDesktop.Application", "Interfaces",
                "IConnectorTrust.cs"))
            .Should().Contain("AgentInputScope.IsAgentInputInFlight(",
                "and the approval has to actually consult the marker");
    }

    private static string AppHostBuilderSource() => Path.Combine(
        RepositoryRoot(), "src", "SranDesktop.App", "Hosting", "AppHostBuilder.cs");

    [Fact]
    public void Every_Allowlisted_File_Still_Exists()
    {
        var present = ProductionSources()
            .Select(Path.GetFileName)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        ProtocolCallAllowlist.Keys.Should().BeSubsetOf(
            present!,
            "a stale allowlist entry silently widens what the next file may do");
    }

    private static string GatewaySource() =>
        Path.Combine(RemoteDirectory(), "McpDurableGateway.cs");

    private static string RemoteDirectory() =>
        Path.Combine(RepositoryRoot(), "src", "SranDesktop.Infrastructure", "Mcp", "Remote");

    private static string[] ProductionSources() =>
        Directory.GetFiles(
                Path.Combine(RepositoryRoot(), "src", "SranDesktop.Infrastructure"),
                "*.cs",
                SearchOption.AllDirectories)
            .Concat(Directory.GetFiles(
                Path.Combine(RepositoryRoot(), "src", "SranDesktop.Application"),
                "*.cs",
                SearchOption.AllDirectories))
            .Concat(Directory.GetFiles(
                Path.Combine(RepositoryRoot(), "src", "SranDesktop.Presentation"),
                "*.cs",
                SearchOption.AllDirectories))
            .Where(file => !file.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}",
                StringComparison.Ordinal))
            .Where(file => !file.Contains($"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}",
                StringComparison.Ordinal))
            .ToArray();

    private static string RepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "SranDesktop.sln")))
            directory = directory.Parent;
        return directory?.FullName
               ?? throw new InvalidOperationException(
                   "The repository root was not found from the test output directory.");
    }
}
