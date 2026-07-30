using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Remote MCP through the production graph, against a server in another process.
///
/// The shape being verified is the same one local MCP has: AgentRun → AgentStep →
/// gateway → endpoint/trust/credential policy → transport → verification →
/// reconciliation. What is different is everything that can go wrong across a
/// socket — a response nobody bounds, a credential that ends up in a log, a
/// server that commits and then stops talking — and those are what this file is
/// mostly about.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class RemoteMcpRuntimeTests : IAsyncLifetime
{
    private const string ConnectorId = "remote-fixture";
    private const string Token = "tok-live-9f3a7c1e5b2d";

    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-remote-{Guid.NewGuid():N}");
    private RemoteMcpFixture _server = null!;
    private ProductionConnectorHost _host = null!;

    public async Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        AgentInputScope.ResetForTests();
        _server = await RemoteMcpFixture.StartAsync();
        _host = NewHost();
        await InstallAsync();
    }

    private ProductionConnectorHost NewHost() => new(
        _root,
        $"--Sran:McpCatalogUrl={_server.BaseUrl}/control/none",
        "--Sran:McpOperationTimeoutSeconds=8",
        "--Sran:ConnectorDialTimeoutSeconds=20");

    // ── Endpoint policy ─────────────────────────────────────────────────────

    [Theory]
    [InlineData("https://mcp.example.com/server", true, null)]
    [InlineData("http://127.0.0.1:9000/server", true, null)]
    [InlineData("http://localhost:9000/server", true, null)]
    [InlineData("http://mcp.example.com/server", false, "EndpointNotEncrypted")]
    [InlineData("ftp://mcp.example.com/server", false, "EndpointSchemeNotAllowed")]
    [InlineData("https://user:pass@mcp.example.com/s", false, "EndpointCarriesCredentials")]
    [InlineData("not-a-url", false, "EndpointMalformed")]
    [InlineData("", false, "EndpointMissing")]
    public void The_Endpoint_Policy_Decides_Where_A_Credential_May_Travel(
        string url, bool allowed, string? rejectionCode)
    {
        var verdict = McpEndpointPolicy.Default.Evaluate(url);

        verdict.Allowed.Should().Be(allowed);
        verdict.RejectionCode.Should().Be(rejectionCode);
    }

    [Fact]
    public void A_Deployment_Can_Narrow_The_Permitted_Hosts()
    {
        var policy = McpEndpointPolicy.Default with { AllowedHosts = ["mcp.corp.example"] };

        policy.Evaluate("https://mcp.corp.example/s").Allowed.Should().BeTrue();
        policy.Evaluate("https://elsewhere.example/s").RejectionCode
            .Should().Be("EndpointHostNotAllowed");
    }

    [Fact]
    public void Plain_Http_To_A_Remote_Host_Is_Refused_Even_Without_A_Host_Allowlist()
    {
        // The common misconfiguration: a self-hosted connector reached over the
        // office network. A credential must not travel in the clear because the
        // network feels internal.
        McpEndpointPolicy.Default.Evaluate("http://mcp.internal.corp/server")
            .RejectionCode.Should().Be("EndpointNotEncrypted");
    }

    // ── Protocol through the gateway ────────────────────────────────────────

    [Fact]
    public async Task Every_Remote_Protocol_Operation_Is_A_Durable_Step()
    {
        var operations = new[]
        {
            Request(McpOperationKind.Initialize),
            Request(McpOperationKind.ToolsList),
            Request(McpOperationKind.ResourcesList),
            Request(McpOperationKind.ResourcesRead, resource: "remote://readme"),
            Request(McpOperationKind.PromptsList),
            Request(McpOperationKind.PromptsGet, promptName: "summarize"),
            Request(McpOperationKind.ToolsCall, tool: "ping")
        };

        var results = new List<McpOperationResult>();
        foreach (var operation in operations)
            results.Add(await _host.Gateway.ExecuteOwnedAsync(operation, "test:remote"));

        results.Should().OnlyContain(item => item.Status == McpOperationStatus.Succeeded,
            string.Join(" | ", results.Select(item =>
                $"{item.Operation}:{item.FailureCode}:{item.FailureReason}")));
        foreach (var result in results)
        {
            result.ActionId.Should().NotBeNullOrEmpty(
                $"{result.Operation} must leave a step behind it");
            (await _host.Tasks.GetActionAsync(result.ActionId!)).Should().NotBeNull();
        }
        results.Select(item => item.ActionId).Distinct().Should().HaveCount(operations.Length);
    }

    [Fact]
    public async Task Capability_Negotiation_Reports_What_The_Server_Declared()
    {
        var result = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.Initialize), "test:remote");

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.Content.Should().Contain("2025-06-18");
        result.Content.Should().Contain("sran-remote-fixture");
    }

    [Fact]
    public async Task Remote_Output_Arrives_As_An_Untrusted_Observation()
    {
        // The fixture answers resources/read with text that tries to be an
        // instruction. It has to arrive as data.
        var result = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ResourcesRead, resource: "remote://readme"),
            "test:remote");

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.Content.Should().StartWith(UntrustedObservationBoundary.Marker);
        UntrustedObservationBoundary.TryReadEnvelope(result.Content, out var envelope)
            .Should().BeTrue();
        envelope!.TrustLevel.Should().Be("untrusted");
        envelope.ProhibitedUses.Should().Contain("grant_permission");
    }

    [Fact]
    public async Task A_Remote_Prompt_Is_An_Observation_Not_A_System_Instruction()
    {
        var result = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.PromptsGet, promptName: "summarize"), "test:remote");

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        result.Content.Should().StartWith(UntrustedObservationBoundary.Marker,
            "a prompt fetched from a server is content, and content never becomes "
            + "a developer instruction");
    }

    [Fact]
    public async Task Provenance_Is_Recorded_On_Every_Remote_Answer()
    {
        var result = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsCall, tool: "ping"), "test:remote");

        result.Evidence.Should().Contain("provenance=verified");
        result.Evidence.Should().Contain(item =>
            item.StartsWith("server_identity=", StringComparison.Ordinal));
    }

    // ── Deadlines, size limits, reconnect ───────────────────────────────────

    [Fact]
    public async Task A_Server_That_Never_Answers_Hits_The_Deadline_Rather_Than_Hanging()
    {
        // "slow" is not in any approved risk policy, so it classifies as Unknown
        // and the runtime rightly demands approval before it may act at all.
        var context = await ScheduledRunAsync(_host);
        await ApproveActionAsync(_host, context, "slow", "{}");

        var result = await _host.Gateway.ExecuteAsync(
            context, Request(McpOperationKind.ToolsCall, tool: "slow"));

        result.Status.Should().NotBe(McpOperationStatus.Succeeded);
        result.Retryable.Should().BeFalse(
            "a request that went out is never blindly repeated");
        string.Join(" | ", result.Evidence.Append(result.FailureReason ?? ""))
            .Should().ContainAny("did not answer", "dispatch_stage", "reconcile");
    }

    [Fact]
    public async Task A_Response_Larger_Than_The_Limit_Is_Refused_Not_Buffered()
    {
        var result = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsCall, tool: "oversized"), "test:remote");

        result.Status.Should().NotBe(McpOperationStatus.Succeeded,
            "a connector that answers with 32 MB is not making a protocol error, "
            + "it is exhausting this process");
    }

    [Fact]
    public async Task A_Reconnect_Reinitializes_And_Raises_The_Generation()
    {
        var first = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test:remote");
        var reconnect = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.Reconnect), "test:remote");

        first.Status.Should().Be(McpOperationStatus.Succeeded, first.FailureReason);
        reconnect.Status.Should().Be(McpOperationStatus.Succeeded, reconnect.FailureReason);
        reconnect.Content.Should().Contain("generation");
    }

    [Fact]
    public async Task A_Server_That_Dies_Is_Reported_Rather_Than_Retried_Silently()
    {
        (await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test:remote"))
            .Status.Should().Be(McpOperationStatus.Succeeded);

        _server.Kill();

        var result = await _host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsCall, tool: "ping"), "test:remote");
        result.Status.Should().NotBe(McpOperationStatus.Succeeded);
        result.ActionId.Should().NotBeNullOrEmpty(
            "a failure against a dead server is still a durable step");
    }

    // ── Credential integration ──────────────────────────────────────────────

    [Fact]
    public async Task A_Connector_With_A_Connected_Account_Sends_Its_Credential()
    {
        await using var guarded = await RemoteMcpFixture.StartAsync(requiredToken: Token);
        await using var host = NewHostFor(guarded);
        await ConnectAccountAsync(host, guarded, Token);

        var result = await host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test:remote");

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        (await guarded.StatsAsync()).Authorized.Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task A_Connector_Whose_Credential_Is_Rejected_Says_So_Without_Echoing_It()
    {
        await using var guarded = await RemoteMcpFixture.StartAsync(requiredToken: Token);
        await using var host = NewHostFor(guarded);
        // Connect with the WRONG token: the fixture answers 401 and echoes the
        // offending credential, the way a careless real provider does.
        await ConnectAccountAsync(host, guarded, "tok-wrong-2b8e4d6a0c1f");

        var result = await host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test:remote");

        result.Status.Should().NotBe(McpOperationStatus.Succeeded);
        var everything = string.Join(
            " ", result.Evidence.Append(result.FailureReason ?? "").Append(result.Content));
        everything.Should().NotContain("tok-wrong-2b8e4d6a0c1f",
            "a server rejecting a credential frequently echoes it; the client must "
            + "not carry that into a durable record");
        (await guarded.StatsAsync()).Unauthorized.Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task A_Revoked_Account_Stops_The_Connector_Without_Dialling()
    {
        await using var guarded = await RemoteMcpFixture.StartAsync(requiredToken: Token);
        await using var host = NewHostFor(guarded);
        var account = await ConnectAccountAsync(host, guarded, Token);
        var working = await host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "t");
        working.Status.Should().Be(McpOperationStatus.Succeeded, working.FailureReason);
        var authorizedBefore = (await guarded.StatsAsync()).Authorized;
        authorizedBefore.Should().BeGreaterThan(0);

        var broker = host.Services.GetRequiredService<ICredentialBroker>();
        await broker.RevokeAsync(account);
        var result = await host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsCall, tool: "ping"), "t");

        result.Status.Should().NotBe(McpOperationStatus.Succeeded);
        (await guarded.StatsAsync()).Authorized.Should().Be(authorizedBefore,
            "a revoked account must not authenticate again");
    }

    [Fact]
    public async Task No_Authorization_Header_Reaches_The_Durable_Record()
    {
        await using var guarded = await RemoteMcpFixture.StartAsync(requiredToken: Token);
        await using var host = NewHostFor(guarded);
        await ConnectAccountAsync(host, guarded, Token);

        var result = await host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test:remote");
        var events = await host.Runtime.GetEventsAsync(result.RunId!);

        var recorded = string.Join(" ",
            result.Evidence
                .Append(result.Content)
                .Concat(events.Select(item => item.DataJson)));
        recorded.Should().NotContain(Token);
        recorded.Should().NotContain("Bearer " + Token);
    }

    // ── Mutation and reconciliation ─────────────────────────────────────────

    [Fact]
    public async Task A_Remote_Mutation_Records_Its_Intent_Before_It_Dispatches()
    {
        var context = await ScheduledRunAsync(_host);
        const string arguments = """{"name":"remote-report"}""";
        await ApproveActionAsync(_host, context, "create_record", arguments);

        var result = await _host.Gateway.ExecuteAsync(
            context, Request(McpOperationKind.ToolsCall, tool: "create_record",
                arguments: arguments));

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        var intent = await _host.Journal.GetAsync(result.IntentId!);
        intent.Should().NotBeNull();
        intent!.ArgumentsFingerprint.Should().Be(
            McpDispatchIntent.Fingerprint(ConnectorId, "create_record", arguments));
        (await _server.StatsAsync()).Created.Should().Be(1);
    }

    [Fact]
    public async Task A_Remote_Mutation_Whose_Answer_Is_Lost_Is_Reconciled_Not_Repeated()
    {
        // The server commits and then stops answering. The client's deadline
        // fires while the request is genuinely in flight.
        await using var silent = await RemoteMcpFixture.StartAsync(profile: "silent-mutation");
        await using var host = NewHostFor(silent);
        await InstallAsync(host, silent);

        var context = await ScheduledRunAsync(host);
        const string arguments = """{"name":"lost-answer"}""";
        await ApproveActionAsync(host, context, "create_record", arguments);

        var result = await host.Gateway.ExecuteAsync(
            context, Request(McpOperationKind.ToolsCall, tool: "create_record",
                arguments: arguments));

        var stats = await silent.StatsAsync();
        stats.Created.Should().Be(1,
            "the server performed the mutation exactly once");
        result.Status.Should().BeOneOf(
            McpOperationStatus.Succeeded, McpOperationStatus.NeedsUser);
        if (result.Status == McpOperationStatus.Succeeded)
            result.ReconciledWithoutRedispatch.Should().BeTrue(
                "success can only have come from reading the server's state back");
        else
            result.Retryable.Should().BeFalse();
    }

    [Fact]
    public async Task An_Unresolved_Remote_Mutation_Is_Not_Redispatched_After_A_Restart()
    {
        var silent = await RemoteMcpFixture.StartAsync(profile: "silent-mutation");
        // Both hosts share one state directory: the restart has to see the
        // journal the first process wrote, or it is not a restart at all.
        const string slot = "restart-slot";
        var host = NewHostFor(silent, slot);
        await InstallAsync(host, silent);
        const string arguments = """{"name":"survives-restart"}""";

        var context = await ScheduledRunAsync(host);
        await ApproveActionAsync(host, context, "create_record", arguments);

        // The server commits and then goes silent. It is killed WHILE the request
        // is still in flight, so nothing can afterwards establish what it did —
        // which is the state the restart has to survive: an outcome nobody knows,
        // still on disk. Letting the first call finish first would let the
        // reconciler resolve it, and the test would prove something easier.
        var port = silent.Port;
        var dispatch = host.Gateway.ExecuteAsync(
            context, Request(McpOperationKind.ToolsCall, tool: "create_record",
                arguments: arguments));
        await WaitForCreationAsync(silent);
        silent.Kill();
        await dispatch;

        var stillOpen = await host.Journal.GetOpenAsync(ConnectorId);
        stillOpen.Should().NotBeEmpty(
            "an unreachable connector must not clear the record that something "
            + "may have happened");
        await host.DisposeAsync();

        // The app restarts and the same scheduled work fires again. The server is
        // back — and would happily create a second record if asked.
        await using var restarted = await RemoteMcpFixture.StartAsync(
            profile: "default", port: port);
        host = NewHostFor(restarted, slot);
        var second = await ScheduledRunAsync(host);
        await ApproveActionAsync(host, second, "create_record", arguments);
        var repeat = await host.Gateway.ExecuteAsync(
            second, Request(McpOperationKind.ToolsCall, tool: "create_record",
                arguments: arguments));

        repeat.Status.Should().NotBe(McpOperationStatus.Succeeded,
            "an open intent means the outcome is unknown; the answer is to find "
            + "out, never to try again");
        repeat.Evidence.Should().Contain(item =>
            item.Contains("reconcile", StringComparison.Ordinal));
        await host.DisposeAsync();
        await silent.DisposeAsync();
    }

    [Fact]
    public async Task A_Connection_Dropped_After_The_Mutation_Is_An_Unknown_Outcome()
    {
        await using var dropping = await RemoteMcpFixture.StartAsync(profile: "mutate-then-drop");
        await using var host = NewHostFor(dropping);
        await InstallAsync(host, dropping);

        var context = await ScheduledRunAsync(host);
        const string arguments = """{"name":"dropped"}""";
        await ApproveActionAsync(host, context, "create_record", arguments);
        var result = await host.Gateway.ExecuteAsync(
            context, Request(McpOperationKind.ToolsCall, tool: "create_record",
                arguments: arguments));

        (await dropping.StatsAsync()).Created.Should().Be(1);
        result.Status.Should().BeOneOf(
            McpOperationStatus.Succeeded, McpOperationStatus.NeedsUser);
        result.Status.Should().NotBe(McpOperationStatus.FailedNoEffect,
            "the server did act; calling it a clean failure would be a false negative "
            + "as damaging as a false success");
    }

    // ── Scheduler and subagent use the same gateway ─────────────────────────

    [Fact]
    public async Task A_Scheduled_Remote_Read_Runs_As_A_Scheduler_Triggered_Step()
    {
        var context = await ScheduledRunAsync(_host);

        var result = await _host.Gateway.ExecuteAsync(
            context, Request(McpOperationKind.ToolsCall, tool: "ping"));

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        (await _host.Tasks.GetAsync(context.TaskId))!.Task.TriggerType
            .Should().Be(AgentTriggerType.Scheduler);
        (await _host.Tasks.GetActionAsync(result.ActionId!)).Should().NotBeNull();
    }

    [Fact]
    public async Task A_Child_Run_Reaches_A_Remote_Connector_Through_The_Same_Gateway()
    {
        var parent = await ScheduledRunAsync(_host);
        var child = await ChildRunAsync(_host, parent);

        var result = await _host.Gateway.ExecuteAsync(
            child, Request(McpOperationKind.ToolsCall, tool: "ping"));

        result.Status.Should().Be(McpOperationStatus.Succeeded, result.FailureReason);
        var snapshot = await _host.Tasks.GetAsync(child.TaskId);
        snapshot!.Task.ParentRunId.Should().Be(parent.TaskId);
        snapshot.Task.TriggerType.Should().Be(AgentTriggerType.Subagent);
        (await _host.Tasks.GetActionAsync(result.ActionId!))!.TaskId.Should().Be(child.TaskId);
    }

    [Fact]
    public async Task An_Unapproved_Remote_Connector_Is_Refused_Before_Any_Request()
    {
        await using var fresh = await RemoteMcpFixture.StartAsync();
        await using var host = NewHostFor(fresh);
        // Installed without evidence of a person: the record exists, the decision
        // does not.
        await host.Connectors.InstallAsync(ConnectorId);
        var before = (await fresh.StatsAsync()).Requests;

        var result = await host.Gateway.ExecuteOwnedAsync(
            Request(McpOperationKind.ToolsList), "test:remote");

        result.Status.Should().Be(McpOperationStatus.Blocked);
        (await fresh.StatsAsync()).Requests.Should().Be(before,
            "an unapproved connector must not be contacted at all");
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    /// <summary>Waits until the server reports it has actually committed the
    /// mutation. Sleeping a fixed interval instead would make the test's meaning
    /// depend on how loaded the machine is.</summary>
    private static async Task WaitForCreationAsync(RemoteMcpFixture server)
    {
        for (var attempt = 0; attempt < 100; attempt++)
        {
            if ((await server.StatsAsync()).Created > 0)
                return;
            await Task.Delay(100);
        }
        throw new InvalidOperationException(
            "The fixture never reported creating the record.");
    }

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

    /// <summary>
    /// A host over its own state directory. <paramref name="slot"/> names that
    /// directory: two hosts sharing a slot share the journal and the database,
    /// which is what makes a "restart" a restart rather than a fresh install with
    /// no memory of the intent it is supposed to reconcile.
    /// </summary>
    private ProductionConnectorHost NewHostFor(RemoteMcpFixture server, string? slot = null) =>
        new(Path.Combine(_root, slot ?? $"host-{Guid.NewGuid():N}"),
            $"--Sran:McpCatalogUrl={server.BaseUrl}/control/none",
            "--Sran:McpOperationTimeoutSeconds=8",
            "--Sran:ConnectorDialTimeoutSeconds=20");

    private Task InstallAsync() => InstallAsync(_host, _server);

    private static async Task InstallAsync(
        ProductionConnectorHost host, RemoteMcpFixture server)
    {
        await host.Connectors.InstallCustomAsync(
            new McpConnector
            {
                Id = ConnectorId,
                Name = "Remote Fixture",
                Transport = McpConnector.TransportHttp,
                ServerUrl = server.ServerUrl,
                AuthType = "none"
            },
            evidence: UserApprovalEvidence.FromUserGesture(
                "user:test", "add connector"));
    }

    /// <summary>Connects an account whose access token is <paramref name="token"/>
    /// and points the connector's install record at it.</summary>
    private static async Task<ConnectorAccountRef> ConnectAccountAsync(
        ProductionConnectorHost host, RemoteMcpFixture server, string token)
    {
        await InstallAsync(host, server);
        var account = ConnectorAccountRef.For(ConnectorId, "ana@example.test");
        var store = host.Services.GetRequiredService<IConnectorCredentialStore>();
        await store.SaveAsync(
            new ConnectorCredentialDescriptor
            {
                Account = account,
                DisplayName = "ana@example.test",
                Kind = ConnectorCredentialKind.OAuth2,
                Scopes = [],
                State = ConnectorCredentialState.Active
            },
            token,
            refreshToken: null);
        return account;
    }

    private static async Task ApproveActionAsync(
        ProductionConnectorHost host,
        DurableTurnContext context,
        string toolName,
        string argumentsJson) =>
        await host.Runtime.RecordApprovalAsync(
            context,
            $"approval-{Guid.NewGuid():N}",
            $"mcp.{ConnectorId}.{toolName}",
            argumentsJson,
            $"connector:{ConnectorId}",
            granted: true,
            DateTimeOffset.UtcNow.AddMinutes(5));

    private static async Task<DurableTurnContext> ScheduledRunAsync(
        ProductionConnectorHost host)
    {
        var conversationId = await NewConversationAsync(host, "schedule");
        return await host.Runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Scheduler,
            "use the remote connector to work with records",
            $"conversation:{conversationId}",
            SecurityContextJson: """{"unattended":true,"consent_fallback":"deny"}""",
            PermissionContextJson: """{"unattended":true,"allow_stored_scope_only":true}""",
            OperationIsFullySpecified: true));
    }

    private static async Task<DurableTurnContext> ChildRunAsync(
        ProductionConnectorHost host, DurableTurnContext parent)
    {
        var conversationId = await NewConversationAsync(host, "child");
        return await host.Runtime.CreateRunAsync(new DurableRunRequest(
            conversationId,
            AgentTriggerType.Subagent,
            "read from the remote connector",
            $"conversation:{conversationId}",
            SecurityContextJson: """{"unattended":true,"consent_fallback":"deny"}""",
            PermissionContextJson: """{"unattended":true,"allow_stored_scope_only":true}""",
            ParentRunId: parent.TaskId,
            RecursionDepth: 1,
            OperationIsFullySpecified: true));
    }

    private static async Task<string> NewConversationAsync(
        ProductionConnectorHost host, string prefix)
    {
        var conversationId = $"{prefix}-{Guid.NewGuid():N}";
        await host.Services.GetRequiredService<IConversationStore>()
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

    public async Task DisposeAsync()
    {
        await _host.DisposeAsync();
        await _server.DisposeAsync();
        await ProductionConnectorHost.DeletePatientlyAsync(_root);
    }
}
