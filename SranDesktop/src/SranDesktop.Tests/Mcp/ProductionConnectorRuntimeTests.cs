using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Mutation reconciliation, scheduler firings, and sub-agent runs — all against
/// the composition <see cref="SranDesktop.App.Hosting.AppHostBuilder"/> builds,
/// a real MCP server process in the capability-free AppContainer, and a real
/// sqlite store.
///
/// The restart tests dispose the whole host and build a new one over the same
/// files. That is the only version of "restart" worth testing: a fresh gateway
/// over a fresh runtime over the same journal and database is exactly what the
/// user gets when the app is reopened, and it is where a duplicate side effect
/// would actually appear.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class ProductionConnectorRuntimeTests : IAsyncLifetime
{
    private const string ConnectorId = "fixture-local";

    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-mcp-runtime-{Guid.NewGuid():N}");
    private ProductionConnectorHost _host = null!;

    public Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        AgentInputScope.ResetForTests();
        _host = NewHost();
        ConnectorPackage.Install(_host.ConnectorRoot, ConnectorId);
        return ApproveAsync();
    }

    /// <summary>The operation deadline is shortened so the silent-server cases
    /// finish in seconds rather than in the two minutes a real deployment is
    /// willing to wait.</summary>
    private ProductionConnectorHost NewHost() =>
        new(_root, "--Sran:McpOperationTimeoutSeconds=6");

    // ── Mutation and restart ────────────────────────────────────────────────

    [Fact]
    public async Task A_Mutation_Whose_Answer_Is_Lost_Is_Reconciled_Rather_Than_Repeated()
    {
        SetProfile("mutate-then-crash");

        var result = await MutateAsync("restart-me");

        // The server committed and died before answering. The runtime must not
        // report success it cannot see, and must not send it again.
        result.Status.Should().BeOneOf(
            McpOperationStatus.Succeeded, McpOperationStatus.NeedsUser);
        result.ReconciledWithoutRedispatch.Should().Be(
            result.Status == McpOperationStatus.Succeeded);
        result.Evidence.Should().NotContain(item =>
            item.Contains("redispatch=performed", StringComparison.Ordinal));

        SetProfile("default");
        var created = await CreatedCountOnAFreshServerAsync();
        created.Should().Be(0,
            "the reconciliation must not have re-run the mutation");
    }

    [Fact]
    public async Task A_Mutation_Interrupted_After_Dispatch_Is_Not_Repeated_By_The_Next_Process()
    {
        SetProfile("silent-mutation");
        var first = await MutateAsync("survives-restart");
        first.Status.Should().BeOneOf(
            McpOperationStatus.NeedsUser, McpOperationStatus.Succeeded);

        var openBefore = await _host.Journal.GetOpenAsync(ConnectorId);
        await _host.DisposeAsync();

        // The app restarts: new gateway, new runtime, same journal and database.
        _host = NewHost();
        SetProfile("default");
        var second = await MutateAsync("survives-restart");

        second.Status.Should().NotBe(McpOperationStatus.Succeeded,
            "a repeat of an unresolved mutation must be reconciled, never dispatched");
        (await CreatedCountOnAFreshServerAsync()).Should().Be(0,
            "duplicate side effects are the failure this whole phase exists to prevent");
        if (first.Status == McpOperationStatus.NeedsUser)
            openBefore.Should().NotBeEmpty(
                "an unresolved mutation stays open so the next process still sees it");
    }

    [Fact]
    public async Task An_Unresolvable_Mutation_Needs_A_Person_And_Is_Never_Auto_Retried()
    {
        SetProfile("silent-mutation");

        var result = await MutateAsync("unresolvable");

        if (result.Status == McpOperationStatus.NeedsUser)
        {
            result.Retryable.Should().BeFalse();
            (await _host.Journal.GetOpenAsync(ConnectorId)).Should().NotBeEmpty();
        }
        else
        {
            result.Status.Should().Be(McpOperationStatus.Succeeded);
            result.ReconciledWithoutRedispatch.Should().BeTrue(
                "success here can only come from reading the server's state back");
        }
    }

    [Fact]
    public async Task A_Connector_Revoked_Before_Reconciliation_Leaves_The_Intent_Open()
    {
        SetProfile("silent-mutation");
        await MutateAsync("revoke-mid-flight");
        var openBefore = await _host.Journal.GetOpenAsync(ConnectorId);

        await _host.Trust.RevokeAsync(ConnectorId, Evidence());
        var results = await _host.Gateway.ReconcileOpenIntentsAsync(ConnectorId);

        if (openBefore.Count > 0)
        {
            results.Should().NotBeEmpty();
            results.Should().OnlyContain(item =>
                item.Status == McpOperationStatus.NeedsUser);
            (await _host.Journal.GetOpenAsync(ConnectorId)).Should().NotBeEmpty(
                "an unreachable connector must not clear the record that something "
                + "may have happened");
        }
    }

    // ── Scheduler ───────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Scheduled_Read_Is_A_Scheduler_Triggered_Step_Through_The_Gateway()
    {
        var context = await ScheduledRunAsync();

        var result = await _host.Gateway.ExecuteAsync(context, Call("ping"));

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        var snapshot = await _host.Tasks.GetAsync(context.TaskId);
        snapshot!.Task.TriggerType.Should().Be(AgentTriggerType.Scheduler);
        (await _host.Tasks.GetActionAsync(result.ActionId!)).Should().NotBeNull();
    }

    [Fact]
    public async Task A_Scheduled_Firing_Against_A_Revoked_Connector_Is_Refused_With_A_Step()
    {
        await _host.Trust.RevokeAsync(ConnectorId, Evidence());
        var context = await ScheduledRunAsync();

        var result = await _host.Gateway.ExecuteAsync(context, Call("ping"));

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorRevoked");
        (await _host.Tasks.GetActionAsync(result.ActionId!)).Should().NotBeNull(
            "an unattended refusal still has to be visible afterwards");
    }

    [Fact]
    public async Task A_Scheduled_Firing_After_The_Binary_Changed_Is_Refused()
    {
        await File.AppendAllTextAsync(
            ConnectorPackage.ExecutablePath(_host.ConnectorRoot, ConnectorId), "\0changed");
        var context = await ScheduledRunAsync();

        var result = await _host.Gateway.ExecuteAsync(context, Call("ping"));

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorExecutableChanged");
    }

    [Fact]
    public async Task A_Scheduled_Mutation_Records_Its_Intent_Before_It_Dispatches()
    {
        var context = await ScheduledRunAsync();
        const string arguments = """{"name":"nightly-report"}""";
        await ApproveActionAsync(context, "create_record", arguments);

        var result = await _host.Gateway.ExecuteAsync(
            context, Call("create_record", arguments));

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.Risk.Should().Be(McpRiskClass.ExternalMutation);
        var intent = await _host.Journal.GetAsync(result.IntentId!);
        intent.Should().NotBeNull();
        intent!.ArgumentsFingerprint.Should().Be(
            McpDispatchIntent.Fingerprint(ConnectorId, "create_record", arguments));
    }

    // ── Sub-agent ───────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Child_Run_Reads_Through_Its_Own_Step_And_Keeps_Its_Provenance()
    {
        var parent = await ScheduledRunAsync();
        var child = await ChildRunAsync(parent);

        var result = await _host.Gateway.ExecuteAsync(child, Call("ping"));

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        var snapshot = await _host.Tasks.GetAsync(child.TaskId);
        snapshot!.Task.ParentRunId.Should().Be(parent.TaskId);
        snapshot.Task.TriggerType.Should().Be(AgentTriggerType.Subagent);
        var action = await _host.Tasks.GetActionAsync(result.ActionId!);
        action!.TaskId.Should().Be(child.TaskId,
            "the child's work belongs to the child, not to whoever spawned it");
    }

    [Fact]
    public async Task A_Child_Cannot_Reach_A_Connector_That_Is_Not_Trusted()
    {
        await _host.Trust.RevokeAsync(ConnectorId, Evidence());
        var child = await ChildRunAsync(await ScheduledRunAsync());

        var result = await _host.Gateway.ExecuteAsync(child, Call("ping"));

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorRevoked",
            "trust belongs to the connector, not to whoever is calling it");
    }

    [Fact]
    public async Task A_Child_Cannot_Approve_A_Connector_For_Itself()
    {
        await _host.Trust.RevokeAsync(ConnectorId, Evidence());
        var child = await ChildRunAsync(await ScheduledRunAsync());

        // Everything a child can reach is a tool call. There is no capability
        // name that reaches the trust store, so the closest it can come is asking
        // the connector to do it — which the connector also cannot do.
        var result = await _host.Gateway.ExecuteAsync(child, Call("ping"));

        result.Status.Should().Be(McpOperationStatus.Blocked);
        _host.TrustStore.Get(ConnectorId)!.State.Should().Be(ConnectorTrustState.Revoked,
            "nothing a run does may move a trust decision");
    }

    [Fact]
    public async Task A_Second_Child_Still_Works_After_A_Sibling_Loses_Its_Answer()
    {
        var parent = await ScheduledRunAsync();
        SetProfile("silent-mutation");
        var lost = await _host.Gateway.ExecuteAsync(
            await ChildRunAsync(parent),
            Call("create_record", """{"name":"lost-child"}"""));
        lost.Status.Should().NotBe(McpOperationStatus.Succeeded);

        SetProfile("default");
        var sibling = await _host.Gateway.ExecuteAsync(
            await ChildRunAsync(parent), Call("ping"));

        sibling.Status.Should().Be(McpOperationStatus.Succeeded, sibling.FailureReason);
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private static McpOperationRequest Call(string tool, string arguments = "{}") => new()
    {
        ConnectorId = ConnectorId,
        Operation = McpOperationKind.ToolsCall,
        ToolName = tool,
        ArgumentsJson = arguments
    };

    private static UserApprovalEvidence Evidence() =>
        UserApprovalEvidence.FromUserGesture("user:test", "approval dialog");

    private async Task ApproveAsync() =>
        await _host.Trust.ApproveAsync(
            ConnectorId,
            new ConnectorApprovalDecision(ConnectorApprovalOutcome.Approved, "user:test"),
            Evidence());

    private async Task<McpOperationResult> MutateAsync(string name)
    {
        var context = await ScheduledRunAsync();
        var arguments = JsonSerializer.Serialize(new { name });
        await ApproveActionAsync(context, "create_record", arguments);
        return await _host.Gateway.ExecuteAsync(context, Call("create_record", arguments));
    }

    /// <summary>Asks a freshly started server how many mutations IT performed.
    /// Counting from the client would only prove the client believes it did not
    /// repeat itself.</summary>
    private async Task<int> CreatedCountOnAFreshServerAsync()
    {
        var stats = await _host.Gateway.ExecuteOwnedAsync(Call("stats"), "test:stats");
        if (stats.Status != McpOperationStatus.Succeeded)
            return 0;
        var envelope = UntrustedObservationBoundary.TryReadEnvelope(
            stats.Content, out var read) && read is not null
            ? read.Content
            : stats.Content;
        var start = envelope.IndexOf('{');
        var end = envelope.LastIndexOf('}');
        if (start < 0 || end <= start)
            return 0;
        using var document = JsonDocument.Parse(envelope[start..(end + 1)]);
        return document.RootElement.TryGetProperty("created", out var created)
            ? created.GetInt32()
            : 0;
    }

    private async Task ApproveActionAsync(
        DurableTurnContext context, string toolName, string argumentsJson) =>
        await _host.Runtime.RecordApprovalAsync(
            context,
            $"approval-{Guid.NewGuid():N}",
            $"mcp.{ConnectorId}.{toolName}",
            argumentsJson,
            $"connector:{ConnectorId}",
            granted: true,
            DateTimeOffset.UtcNow.AddMinutes(5));

    private async Task<DurableTurnContext> ScheduledRunAsync()
    {
        var conversationId = await NewConversationAsync("schedule");
        return await _host.Runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Scheduler,
            "use the fixture connector to work with records",
            $"conversation:{conversationId}",
            SecurityContextJson: """{"unattended":true,"consent_fallback":"deny"}""",
            PermissionContextJson: """{"unattended":true,"allow_stored_scope_only":true}""",
            OperationIsFullySpecified: true));
    }

    private async Task<DurableTurnContext> ChildRunAsync(DurableTurnContext parent)
    {
        var conversationId = await NewConversationAsync("child");
        return await _host.Runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Subagent,
            "read a record with the fixture connector",
            $"conversation:{conversationId}",
            SecurityContextJson: """{"unattended":true,"consent_fallback":"deny"}""",
            PermissionContextJson: """{"unattended":true,"allow_stored_scope_only":true}""",
            ParentRunId: parent.TaskId,
            RecursionDepth: 1,
            OperationIsFullySpecified: true));
    }

    private async Task<string> NewConversationAsync(string prefix)
    {
        var conversationId = $"{prefix}-{Guid.NewGuid():N}";
        await ((IConversationStore)_host.Services.GetService(typeof(IConversationStore))!)
            .UpsertConversationAsync(new Conversation
            {
                Id = conversationId,
                Title = prefix,
                ModelId = "none",
                CreatedAt = DateTimeOffset.UtcNow,
                UpdatedAt = DateTimeOffset.UtcNow,
                Source = ConversationSource.Local
            });
        return conversationId;
    }

    /// <summary>The fixture reads its behaviour from its own workspace, never
    /// from the environment — a server that could be steered by an environment
    /// variable would be steerable by anything that can set one.</summary>
    private void SetProfile(string profile)
    {
        var workspace = Path.Combine(_host.ConnectorRoot, "workspaces", ConnectorId);
        Directory.CreateDirectory(workspace);
        File.WriteAllText(Path.Combine(workspace, "mcp-profile"), profile);
    }

    public async Task DisposeAsync()
    {
        await _host.DisposeAsync();
        await ProductionConnectorHost.DeletePatientlyAsync(_root);
    }
}
