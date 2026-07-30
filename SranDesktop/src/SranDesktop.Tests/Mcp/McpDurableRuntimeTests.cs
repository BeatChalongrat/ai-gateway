using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Remote;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Phase 4.1B: MCP operations through the durable runtime.
///
/// Everything here runs against a REAL MCP server process in the capability-free
/// AppContainer and a REAL sqlite-backed durable runtime. The claims being made —
/// "there is an AgentStep", "the intent was written before dispatch", "the
/// mutation was reconciled rather than repeated" — are only worth anything if
/// they are observed on the actual stores and the actual process boundary, so
/// there are no mock transports and no in-memory task store.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class McpDurableRuntimeTests : IAsyncLifetime
{
    private const string ConnectorId = "fixture-connector";

    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-mcp-durable-{Guid.NewGuid():N}");
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

        var image = Path.Combine(AppContext.BaseDirectory, "workers", "mcp-fixture");
        var executable = Path.Combine(image, "SranDesktop.McpFixtureServer.dll");
        _installation = new ConnectorInstallationPolicy([AppContext.BaseDirectory]);
        _manifest = ApprovedManifest(executable);
        _riskPolicy = DeclaredPolicy();

        BuildGateway();
        SetProfile("default");
        return Task.CompletedTask;
    }

    // ── Group A: every operation is a durable step ──────────────────────────

    [Theory]
    [InlineData(McpOperationKind.Initialize)]
    [InlineData(McpOperationKind.ToolsList)]
    [InlineData(McpOperationKind.ResourcesList)]
    [InlineData(McpOperationKind.PromptsList)]
    public async Task Every_Protocol_Operation_Runs_As_An_AgentStep(McpOperationKind kind)
    {
        var context = await StartRunAsync();

        var result = await _gateway.ExecuteAsync(
            context, new McpOperationRequest { ConnectorId = ConnectorId, Operation = kind });

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.ActionId.Should().NotBeNullOrEmpty(
            "an MCP operation without a durable action has no step behind it");
        (await _store.GetActionAsync(result.ActionId!)).Should().NotBeNull();
        (await _runtime.GetEventsAsync(context.TaskId))
            .Should().Contain(item => item.EventType == "ActionDispatching"
                                      && item.ActionId == result.ActionId);
    }

    [Fact]
    public async Task Tools_Call_Resources_Read_And_Prompts_Get_Each_Leave_Their_Own_Step()
    {
        // A run's plan carries the steps its goal needs; three unrelated
        // connector operations are three pieces of work, so each gets its own
        // run rather than being crammed onto one step.
        var call = await _gateway.ExecuteAsync(await StartRunAsync(), new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "get_record",
            ArgumentsJson = """{"id":"rec-absent"}"""
        });
        var read = await _gateway.ExecuteAsync(await StartRunAsync(), new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ResourcesRead,
            ResourceUri = "fixture://readme"
        });
        var prompt = await _gateway.ExecuteAsync(await StartRunAsync(), new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.PromptsGet,
            PromptName = "summarize"
        });

        new[] { call, read, prompt }.Should().OnlyContain(
            item => item.Status == McpOperationStatus.Succeeded);
        var actionIds = new[] { call.ActionId, read.ActionId, prompt.ActionId };
        actionIds.Should().OnlyContain(id => !string.IsNullOrEmpty(id));
        actionIds.Distinct().Should().HaveCount(3, "each operation is its own step");
        read.Content.Should().StartWith(
            UntrustedObservationBoundary.Marker,
            "resource content is an observation, never an instruction");
    }

    [Fact]
    public async Task A_Refusal_Before_Dispatch_Still_Leaves_A_Durable_Step()
    {
        _manifest = _manifest with { TrustState = ConnectorTrustState.Revoked };
        BuildGateway();
        var context = await StartRunAsync();

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsList
        });

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorRevoked");
        (await _store.GetActionAsync(result.ActionId!)).Should().NotBeNull(
            "even a refusal is a step somebody can audit");
    }

    [Fact]
    public async Task An_Owned_Run_Is_Created_For_Connector_Work_Nobody_Asked_For()
    {
        var result = await _gateway.ExecuteOwnedAsync(
            new McpOperationRequest
            {
                ConnectorId = ConnectorId,
                Operation = McpOperationKind.ToolsList
            },
            McpDurableGateway.LifecycleOwnerScope);

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.RunId.Should().NotBeNullOrEmpty();
        var snapshot = await _store.GetAsync(result.RunId!);
        snapshot.Should().NotBeNull();
        snapshot!.Task.TriggerType.Should().Be(AgentTriggerType.Api);
        (await _store.GetActionAsync(result.ActionId!)).Should().NotBeNull();
    }

    // ── Group B: the protocol feature matrix is honest ──────────────────────

    [Theory]
    [InlineData(McpOperationKind.ToolsListPaginated)]
    [InlineData(McpOperationKind.ResourcesSubscribe)]
    [InlineData(McpOperationKind.CompletionComplete)]
    public async Task An_Unimplemented_Feature_Is_Named_And_Refused(McpOperationKind kind)
    {
        var context = await StartRunAsync();

        var result = await _gateway.ExecuteAsync(
            context, new McpOperationRequest { ConnectorId = ConnectorId, Operation = kind });

        result.Status.Should().Be(
            McpOperationStatus.CapabilityUnavailable,
            "an unsupported feature must be reported, never silently skipped");
        result.FailureCode.Should().Be("CapabilityUnavailable");
        result.FailureReason.Should().NotBeNullOrEmpty();
        McpProtocolFeatureMatrix.IsImplemented(kind).Should().BeFalse();
    }

    [Fact]
    public async Task A_Capability_The_Server_Never_Declared_Is_Not_Attempted()
    {
        SetProfile("tools-only");
        var context = await StartRunAsync();

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ResourcesList
        });

        result.Status.Should().Be(McpOperationStatus.CapabilityUnavailable);
        result.FailureReason.Should().Contain("resources");
    }

    [Fact]
    public async Task Reconnect_Reinitializes_And_Raises_The_Generation()
    {
        var context = await StartRunAsync();
        var first = await _sessions!.AcquireAsync(ConnectorId);

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.Reconnect
        });
        var second = await _sessions!.AcquireAsync(ConnectorId);

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        second.Generation.Should().BeGreaterThan(
            first.Generation,
            "a reconnect must invalidate every handle from the previous server");
        second.Client.Initialized.Should().BeTrue();
        second.Client.NegotiatedProtocolVersion.Should().Be(
            McpProtocolFeatureMatrix.PreferredVersion);
    }

    // ── Group C: trust is enforced at launch, every time ────────────────────

    [Fact]
    public async Task A_Connector_Whose_Executable_Changed_Never_Starts()
    {
        // A binary swapped after approval, in a location that IS controlled —
        // so the only thing standing between it and a launch is the hash.
        var installRoot = Path.Combine(_root, "install");
        Directory.CreateDirectory(installRoot);
        var executable = Path.Combine(installRoot, "swapped-server.dll");
        await File.WriteAllTextAsync(executable, "the approved binary");
        _installation = new ConnectorInstallationPolicy([installRoot]);
        _manifest = ApprovedManifest(executable);
        await File.WriteAllTextAsync(executable, "a different binary entirely");
        BuildGateway();
        var context = await StartRunAsync();

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsList
        });

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorExecutableChanged");
        result.Evidence.Should().Contain("trust_code=ConnectorExecutableChanged");
    }

    [Fact]
    public async Task A_Connector_Asking_For_More_Than_Was_Approved_Never_Starts()
    {
        _manifest = _manifest with
        {
            RequestedCapabilities = ["workspace.read", "network.outbound"],
            ApprovedCapabilities = ["workspace.read"],
            ApprovedManifestFingerprint = null
        };
        BuildGateway();
        var context = await StartRunAsync();

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsList
        });

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorPermissionExpanded");
    }

    [Fact]
    public async Task An_Uninstalled_Connector_Is_Refused_Rather_Than_Dialed()
    {
        var context = await StartRunAsync();

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = "never-installed",
            Operation = McpOperationKind.ToolsList
        });

        result.Status.Should().Be(McpOperationStatus.Blocked);
        result.FailureCode.Should().Be("ConnectorNotInstalled");
    }

    // ── Group D: mutation reconciliation ────────────────────────────────────

    [Fact]
    public async Task A_Mutation_Whose_Answer_Is_Lost_Is_Reconciled_Not_Repeated()
    {
        // The server creates the record and dies before answering. Its backend
        // survives, which is what a real server with an external store looks
        // like from here.
        SetProfile("mutate-then-crash");
        var context = await StartRunAsync();
        var arguments = """{"name":"invoice-9001"}""";
        await ApproveAsync(context, "create_record", arguments);

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "create_record",
            ArgumentsJson = arguments
        });

        // The successor process is a healthy server whose backend holds the
        // record the dead one committed.
        result.Status.Should().BeOneOf(
            McpOperationStatus.Succeeded, McpOperationStatus.NeedsUser);
        result.Status.Should().NotBe(McpOperationStatus.FailedNoEffect,
            "the record was created, so 'nothing happened' would be a lie");

        if (result.Status == McpOperationStatus.Succeeded)
        {
            result.ReconciledWithoutRedispatch.Should().BeTrue();
            result.Evidence.Should().Contain("redispatch=none");
        }

        // Whatever the verdict, the mutation must not have been sent twice.
        SetProfile("default");
        var stats = await CreateCountOnFreshSessionAsync();
        stats.Should().Be(0, "reconciliation must never redispatch the mutation");
    }

    [Fact]
    public async Task An_Unresolvable_Mutation_Becomes_NeedsUser_And_Stays_Open()
    {
        SetProfile("crash-after-dispatch");
        // No verification tool and no fingerprint record: nothing can establish
        // the truth, so a person has to.
        _riskPolicy = _riskPolicy with
        {
            VerificationTools = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        };
        BuildGateway();
        var context = await StartRunAsync();
        var arguments = """{"name":"unknowable"}""";
        await ApproveAsync(context, "create_record", arguments);

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "create_record",
            ArgumentsJson = arguments
        });

        result.Status.Should().Be(McpOperationStatus.NeedsUser);
        result.Retryable.Should().BeFalse();
        var open = await _journal.GetOpenAsync(ConnectorId);
        open.Should().ContainSingle(item => item.ToolName == "create_record",
            "an unresolved mutation must still be there after the next restart");
    }

    [Fact]
    public async Task An_Open_Intent_Blocks_A_Second_Dispatch_Of_The_Same_Mutation()
    {
        var arguments = """{"name":"already-in-flight"}""";
        var fingerprint = McpDispatchIntent.Fingerprint(
            ConnectorId, "create_record", arguments);
        // Stands in for an intent written by a process that died mid-call.
        await _journal.RecordAsync(new McpDispatchIntent
        {
            IntentId = "mcpintent-restart",
            RunId = "run-previous",
            ActionId = "action-previous",
            ToolCallId = "call-previous",
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "create_record",
            Risk = McpRiskClass.ExternalMutation,
            ArgumentsFingerprint = fingerprint,
            IdempotencyKey = "restart-key",
            CreatedUtc = DateTimeOffset.UtcNow.AddMinutes(-1),
            VerificationTool = "get_record",
            Phase = McpDispatchPhase.Dispatched
        });
        var context = await StartRunAsync();
        await ApproveAsync(context, "create_record", arguments);

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ToolsCall,
            ToolName = "create_record",
            ArgumentsJson = arguments
        });

        result.Status.Should().NotBe(
            McpOperationStatus.Succeeded,
            "nothing was confirmed, so success must not be claimed");
        (await CreateCountOnFreshSessionAsync()).Should().Be(
            0, "a restart must reconcile the open intent, never dispatch it again");
    }

    [Fact]
    public async Task Open_Read_Intents_Are_Discarded_And_Mutations_Escalate()
    {
        await _journal.RecordAsync(OpenIntent("get_record", McpRiskClass.IdempotentRead));
        await _journal.RecordAsync(OpenIntent("create_record", McpRiskClass.ExternalMutation));

        var results = await _gateway.ReconcileOpenIntentsAsync(ConnectorId);

        results.Should().HaveCount(2);
        results.Single(item => item.Operation == McpOperationKind.ToolsCall
                               && item.Risk == McpRiskClass.IdempotentRead)
            .Status.Should().Be(McpOperationStatus.FailedNoEffect);
        results.Single(item => item.Risk == McpRiskClass.ExternalMutation)
            .Status.Should().BeOneOf(
                McpOperationStatus.NeedsUser, McpOperationStatus.FailedNoEffect);
    }

    // ── Group E: the connector cannot talk its way out of the sandbox ───────

    [Fact]
    public async Task Hostile_Resource_Content_Arrives_As_Data_And_Grants_Nothing()
    {
        var context = await StartRunAsync();

        var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
        {
            ConnectorId = ConnectorId,
            Operation = McpOperationKind.ResourcesRead,
            ResourceUri = "fixture://secret-looking"
        });

        result.Status.Should().Be(McpOperationStatus.Succeeded);
        result.Content.Should().StartWith(UntrustedObservationBoundary.Marker);
        // The trust decision is still whatever the manifest says; nothing the
        // server emitted changed it.
        ConnectorTrustPolicy.Evaluate(_manifest, _installation).State
            .Should().Be(ConnectorTrustState.Trusted);
    }

    [Fact]
    public async Task A_Server_Cannot_Read_Host_Secrets_Through_The_Gateway()
    {
        const string name = "SRAN_MCP_GATEWAY_SECRET";
        Environment.SetEnvironmentVariable(name, "gateway_secret_must_not_leak_5f2a");
        try
        {
            var context = await StartRunAsync();

            var result = await _gateway.ExecuteAsync(context, new McpOperationRequest
            {
                ConnectorId = ConnectorId,
                Operation = McpOperationKind.ToolsCall,
                ToolName = "read-env",
                ArgumentsJson = $$"""{"name":"{{name}}"}"""
            });

            result.Content.Should().NotContain("gateway_secret_must_not_leak_5f2a");
            result.Content.Should().Contain("(absent)");
        }
        finally
        {
            Environment.SetEnvironmentVariable(name, null);
        }
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

    private static ConnectorRiskPolicy DeclaredPolicy() => new()
    {
        ConnectorId = ConnectorId,
        PolicyDeclared = true,
        ReadOnlyTools = ["get_record", "ping", "echo", "stats", "lookup_by_idempotency_key"],
        ExternalMutationTools = ["create_record"],
        VerificationTools = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["create_record"] = "get_record"
        }
    };

    private string Workspace => Path.Combine(_workspaceRoot, ConnectorId);

    private void SetProfile(string profile)
    {
        Directory.CreateDirectory(Workspace);
        File.WriteAllText(Path.Combine(Workspace, "mcp-profile"), profile);
    }

    /// <summary>Mutations are consequential, so the durable runtime demands a
    /// scoped approval before it will even prepare the action. Granting it here
    /// is what an approved consent dialog does in the product.</summary>
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

    private async Task<DurableTurnContext> StartRunAsync()
    {
        var conversationId = await NewConversationAsync("mcp-conversation");
        return await _runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Chat,
            "use the fixture connector to work with records",
            $"conversation:{conversationId}"));
    }

    private McpDispatchIntent OpenIntent(string toolName, McpRiskClass risk) => new()
    {
        IntentId = $"mcpintent-{Guid.NewGuid():N}",
        RunId = "run-open",
        ActionId = "action-open",
        ToolCallId = "call-open",
        ConnectorId = ConnectorId,
        Operation = McpOperationKind.ToolsCall,
        ToolName = toolName,
        Risk = risk,
        ArgumentsFingerprint = McpDispatchIntent.Fingerprint(ConnectorId, toolName, "{}"),
        IdempotencyKey = $"open-{toolName}",
        CreatedUtc = DateTimeOffset.UtcNow.AddMinutes(-5),
        VerificationTool = "get_record",
        Phase = McpDispatchPhase.Dispatched
    };

    /// <summary>How many mutations a FRESH server process performed. A
    /// reconciliation that quietly redispatched would show up as one.</summary>
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
