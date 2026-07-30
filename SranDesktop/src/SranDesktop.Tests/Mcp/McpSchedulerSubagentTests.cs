using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Remote;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// MCP from the two entry points that are not a chat turn: a scheduled firing
/// and a sub-agent.
///
/// Both matter for the same reason — nobody is watching. An unattended run that
/// loses a mutation's answer must still end in a state a person can act on, and
/// a child run must not be able to reach further than its parent could.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class McpSchedulerSubagentTests : IAsyncLifetime
{
    private const string ConnectorId = "fixture-connector";

    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-mcp-triggers-{Guid.NewGuid():N}");
    private string _workspaceRoot = "";
    private string _databasePath = "";
    private SqliteConversationStore _conversations = null!;
    private SqliteAgentTaskStore _store = null!;
    private DurableTaskRuntime _runtime = null!;
    private McpDispatchJournal _journal = null!;
    private IsolatedMcpConnectorSessionProvider? _sessions;
    private McpDurableGateway _gateway = null!;
    private ConnectorManifest _manifest = null!;
    private ConnectorRiskPolicy _riskPolicy = ConnectorRiskPolicy.Undeclared(ConnectorId);
    private ConnectorInstallationPolicy _installation = null!;

    public Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        _workspaceRoot = Path.Combine(_root, "workspaces");
        Directory.CreateDirectory(_workspaceRoot);
        _databasePath = Path.Combine(_root, "durable.db");
        _conversations = new SqliteConversationStore(_databasePath);
        _store = new SqliteAgentTaskStore(_databasePath);
        _runtime = new DurableTaskRuntime(_store, new GoalInterpreter(), new CapabilityRouter());
        _journal = new McpDispatchJournal(Path.Combine(_root, "mcp-intents.json"));

        var executable = Path.Combine(
            AppContext.BaseDirectory, "workers", "mcp-fixture",
            "SranDesktop.McpFixtureServer.dll");
        _installation = new ConnectorInstallationPolicy([AppContext.BaseDirectory]);
        _manifest = ApprovedManifest(executable);
        _riskPolicy = new ConnectorRiskPolicy
        {
            ConnectorId = ConnectorId,
            PolicyDeclared = true,
            ReadOnlyTools = ["get_record", "ping", "echo", "stats"],
            ExternalMutationTools = ["create_record"],
            VerificationTools = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                ["create_record"] = "get_record"
            }
        };
        BuildGateway();
        SetProfile("default");
        return Task.CompletedTask;
    }

    // ── Scheduler ───────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Scheduled_Read_Runs_As_A_Scheduler_Triggered_AgentStep()
    {
        var context = await ScheduledRunAsync();

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "get_record",
            ArgumentsJson = """{"id":"rec-1"}"""
        });

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        var snapshot = await _store.GetAsync(context.TaskId);
        snapshot!.Task.TriggerType.Should().Be(AgentTriggerType.Scheduler);
        (await _store.GetActionAsync(result.ActionId!)).Should().NotBeNull();
    }

    [Fact]
    public async Task A_Scheduled_Mutation_Persists_Its_Intent_Before_Dispatch()
    {
        var context = await ScheduledRunAsync();
        var arguments = """{"name":"nightly-report"}""";
        await ApproveAsync(context, "create_record", arguments);

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "create_record",
            ArgumentsJson = arguments
        });

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.IntentId.Should().NotBeNullOrEmpty();
        var intent = await _journal.GetAsync(result.IntentId!);
        intent.Should().NotBeNull();
        intent!.Risk.Should().Be(McpRiskClass.ExternalMutation);
        intent.Resolution.Should().Be(McpOperationStatus.Succeeded);
        intent.ArgumentsFingerprint.Should().Be(
            McpDispatchIntent.Fingerprint(ConnectorId, "create_record", arguments));
    }

    [Fact]
    public async Task A_Scheduled_Mutation_Interrupted_After_Dispatch_Does_Not_Repeat_On_Restart()
    {
        // Firing one: the server commits the record and dies before answering.
        SetProfile("mutate-then-crash");
        var first = await ScheduledRunAsync();
        var arguments = """{"name":"nightly-invoice"}""";
        await ApproveAsync(first, "create_record", arguments);

        var interrupted = await _gateway.ExecuteAsync(first, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "create_record",
            ArgumentsJson = arguments
        });
        interrupted.Status.Should().NotBe(
            McpOperationStatus.FailedNoEffect,
            "the record was created, so 'nothing happened' would be false");

        // Restart: a brand new runtime and gateway over the SAME journal and
        // database, exactly as the next process would see them.
        SetProfile("default");
        await RestartAsync();

        var second = await ScheduledRunAsync();
        await ApproveAsync(second, "create_record", arguments);
        var afterRestart = await _gateway.ExecuteAsync(second, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "create_record",
            ArgumentsJson = arguments
        });

        afterRestart.Status.Should().NotBe(
            McpOperationStatus.Succeeded,
            "a repeat of an unresolved mutation must not be reported as a fresh success");
        (await CreateCountOnFreshSessionAsync()).Should().Be(
            0, "duplicate side effect must be zero across the restart");
    }

    // ── Sub-agent ───────────────────────────────────────────────────────────

    [Fact]
    public async Task A_Child_Run_Reads_Through_Its_Own_Durable_Step_With_Provenance()
    {
        var parent = await ChatRunAsync();
        var childConversation = await NewConversationAsync("subagent");
        var child = await _runtime.CreateChildRunAsync(
            parent.TaskId,
            new DurableRunRequest(
                childConversation,
                AgentTriggerType.Subagent,
                "read the fixture connector records",
                $"conversation:{childConversation}"));

        var result = await _gateway.ExecuteAsync(child, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "get_record",
            ArgumentsJson = """{"id":"rec-1"}"""
        });

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.RunId.Should().Be(child.TaskId);
        var snapshot = await _store.GetAsync(child.TaskId);
        snapshot!.Task.ParentRunId.Should().Be(parent.TaskId, "provenance is preserved");
        snapshot.Task.TriggerType.Should().Be(AgentTriggerType.Subagent);
        (await _store.GetActionAsync(result.ActionId!))!.TaskId.Should().Be(child.TaskId);
        (await _runtime.GetEventsAsync(parent.TaskId))
            .Should().Contain(item => item.EventType == "ChildRunCreated");
    }

    [Fact]
    public async Task A_Child_Cannot_Reach_A_Connector_Its_Parent_Was_Not_Allowed()
    {
        // Trust is a property of the connector, not of who is asking — a child
        // run cannot widen it by being a different run.
        _manifest = _manifest with { TrustState = ConnectorTrustState.Revoked };
        BuildGateway();
        var parent = await ChatRunAsync();
        var childConversation = await NewConversationAsync("subagent");
        var child = await _runtime.CreateChildRunAsync(
            parent.TaskId,
            new DurableRunRequest(
                childConversation,
                AgentTriggerType.Subagent,
                "read the fixture connector records",
                $"conversation:{childConversation}"));

        var result = await _gateway.ExecuteAsync(child, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "get_record",
            ArgumentsJson = """{"id":"rec-1"}"""
        });

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorRevoked");
    }

    [Fact]
    public async Task A_Child_Whose_Mutation_Is_Lost_Needs_A_User_And_Leaves_Siblings_Intact()
    {
        var parent = await ChatRunAsync();
        var readerConversation = await NewConversationAsync("subagent-read");
        var reader = await _runtime.CreateChildRunAsync(
            parent.TaskId,
            new DurableRunRequest(
                readerConversation,
                AgentTriggerType.Subagent,
                "read the fixture connector records",
                $"conversation:{readerConversation}"));
        var readResult = await _gateway.ExecuteAsync(reader, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "get_record",
            ArgumentsJson = """{"id":"rec-1"}"""
        });
        readResult.Status.Should().Be(McpOperationStatus.Succeeded, readResult.FailureReason);

        // The sibling's connection dies after dispatch with nothing able to
        // establish what happened.
        SetProfile("crash-after-dispatch");
        _riskPolicy = _riskPolicy with
        {
            VerificationTools = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        };
        BuildGateway();
        var mutatorConversation = await NewConversationAsync("subagent-write");
        var mutator = await _runtime.CreateChildRunAsync(
            parent.TaskId,
            new DurableRunRequest(
                mutatorConversation,
                AgentTriggerType.Subagent,
                "use the fixture connector to work with records",
                $"conversation:{mutatorConversation}"));
        var arguments = """{"name":"child-created"}""";
        await ApproveAsync(mutator, "create_record", arguments);

        var mutation = await _gateway.ExecuteAsync(mutator, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "create_record",
            ArgumentsJson = arguments
        });

        mutation.Status.Should().Be(
            McpOperationStatus.NeedsUser,
            $"{mutation.FailureCode}: {mutation.FailureReason}");
        (await _store.GetAsync(mutator.TaskId))!.Task.Status
            .Should().BeOneOf(AgentTaskStatus.Paused, AgentTaskStatus.NeedsUser);

        // The sibling's completed read is untouched by the failure next door.
        (await _store.GetActionAsync(readResult.ActionId!)).Should().NotBeNull();
        (await _store.GetAsync(reader.TaskId))!.Task.Status
            .Should().NotBe(AgentTaskStatus.Failed);
        (await _journal.GetOpenAsync(ConnectorId))
            .Should().ContainSingle(item => item.RunId == mutator.TaskId,
                "the unresolved mutation stays on the books");
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    /// <summary>A run hangs off a conversation row; the durable store enforces it.</summary>
    private async Task<string> NewConversationAsync(string prefix)
    {
        var conversationId = $"{prefix}-{Guid.NewGuid():N}";
        await _conversations.UpsertConversationAsync(new Conversation
        {
            Id = conversationId,
            Title = "MCP durable test",
            ModelId = "test",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });
        return conversationId;
    }

    private void BuildGateway()
    {
        // Replacing the provider without disposing the old one would strand its
        // live session — and with it a sandbox job directory.
        _sessions?.DisposeAsync().AsTask().GetAwaiter().GetResult();
        _sessions = new IsolatedMcpConnectorSessionProvider(
            id => string.Equals(id, ConnectorId, StringComparison.Ordinal) ? _manifest : null,
            _ => _riskPolicy,
            new IsolatedMcpServerLauncher(_workspaceRoot));
        _gateway = new McpDurableGateway(
            _runtime, _journal, _sessions,
            installation: _installation, conversations: _conversations);
    }

    /// <summary>A new process over the same durable state: new runtime, new
    /// gateway, new sessions, same database and same journal file.</summary>
    private async Task RestartAsync()
    {
        if (_sessions is not null)
            await _sessions.DisposeAsync();
        _conversations = new SqliteConversationStore(_databasePath);
        _store = new SqliteAgentTaskStore(_databasePath);
        _runtime = new DurableTaskRuntime(_store, new GoalInterpreter(), new CapabilityRouter());
        _journal = new McpDispatchJournal(Path.Combine(_root, "mcp-intents.json"));
        BuildGateway();
    }

    private ConnectorManifest ApprovedManifest(string executable)
    {
        var manifest = new ConnectorManifest
        {
            ConnectorId = ConnectorId,
            Name = "Fixture MCP Server",
            Version = "1.0.0",
            ExecutablePath = executable,
            ExecutableSha256 = ConnectorTrustPolicy.HashExecutable(executable),
            RequestedCapabilities = ["workspace.read"],
            ApprovedCapabilities = ["workspace.read"],
            TrustState = ConnectorTrustState.Trusted,
            Enabled = true,
            TrustedAtUtc = DateTimeOffset.UtcNow
        };
        return manifest with
        {
            ApprovedManifestFingerprint = manifest.ComputeManifestFingerprint()
        };
    }

    private string Workspace => Path.Combine(_workspaceRoot, ConnectorId);

    private void SetProfile(string profile)
    {
        Directory.CreateDirectory(Workspace);
        File.WriteAllText(Path.Combine(Workspace, "mcp-profile"), profile);
    }

    private async Task ApproveAsync(
        DurableTurnContext context,
        string toolName,
        string argumentsJson) =>
        await _runtime.RecordApprovalAsync(
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
        return await _runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Scheduler,
            "use the fixture connector to work with records",
            $"conversation:{conversationId}",
            SecurityContextJson: """{"unattended":true,"consent_fallback":"deny"}""",
            PermissionContextJson: """{"unattended":true,"allow_stored_scope_only":true}"""));
    }

    private async Task<DurableTurnContext> ChatRunAsync()
    {
        var conversationId = await NewConversationAsync("chat");
        return await _runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Chat,
            "use the fixture connector to work with records",
            $"conversation:{conversationId}"));
    }

    private async Task<int> CreateCountOnFreshSessionAsync()
    {
        var session = await _sessions!.ReconnectAsync(ConnectorId);
        var stats = await session.Client.CallToolAsync(
            "stats", JsonSerializer.SerializeToElement(new { }));
        using var document = JsonDocument.Parse(
            stats.Content[stats.Content.IndexOf('{')..(stats.Content.LastIndexOf('}') + 1)]);
        return document.RootElement.GetProperty("created").GetInt32();
    }

    public async Task DisposeAsync()
    {
        if (_sessions is not null)
            await _sessions.DisposeAsync();
        for (var attempt = 0; attempt < 5 && Directory.Exists(_root); attempt++)
        {
            try
            {
                Directory.Delete(_root, recursive: true);
            }
            catch (IOException) when (attempt < 4)
            {
                await Task.Delay(50);
            }
            catch (UnauthorizedAccessException) when (attempt < 4)
            {
                await Task.Delay(50);
            }
        }
    }
}
