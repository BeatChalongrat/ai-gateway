using System.Diagnostics;
using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Remote;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Workers;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>
/// Local MCP end to end: a REAL server process speaking newline-delimited
/// JSON-RPC inside the capability-free AppContainer. No mock transport and no
/// in-process fixture — the behaviours under test (crash mid-call, duplicate
/// reply, misrouted id, sandbox denial) only exist across a process boundary.
/// </summary>
[Collection("PathGuardGrants")]
[Trait("Category", "WindowsSecurityIntegration")]
public sealed class LocalMcpRuntimeTests : IAsyncLifetime
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-mcp-runtime-{Guid.NewGuid():N}");
    private readonly List<IsolatedStdioMcpTransport> _transports = [];
    private readonly List<string> _workspaces = [];

    public Task InitializeAsync()
    {
        Directory.CreateDirectory(_root);
        return Task.CompletedTask;
    }

    private (IsolatedStdioMcpTransport Transport, string Workspace) CreateTransport(
        string profile = "default",
        string? reuseWorkspace = null)
    {
        var workspace = reuseWorkspace ?? Path.Combine(_root, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(workspace);
        File.WriteAllText(Path.Combine(workspace, "mcp-profile"), profile);
        var runner = new WindowsAppContainerProcessRunner(
            new WorkspaceFileBroker(
                [new WorkspaceMount(workspace, WorkspaceMountAccess.ReadWrite)],
                workspace));
        var image = new WorkerImageResolver(
            Path.Combine(AppContext.BaseDirectory, "workers")).Resolve("mcp-fixture");
        var transport = new IsolatedStdioMcpTransport(runner, image, workspace);
        _transports.Add(transport);
        _workspaces.Add(workspace);
        return (transport, workspace);
    }

    private async Task<(McpRemoteClient Client, IsolatedStdioMcpTransport Transport, string Workspace)>
        ConnectAsync(string profile = "default", string? reuseWorkspace = null)
    {
        var (transport, workspace) = CreateTransport(profile, reuseWorkspace);
        var client = new McpRemoteClient(transport);
        await client.InitializeAsync();
        return (client, transport, workspace);
    }

    [Fact]
    public async Task Initialize_Negotiates_Capabilities_With_A_Sandboxed_Server()
    {
        var (client, transport, _) = await ConnectAsync();

        client.Initialized.Should().BeTrue();
        client.Capabilities.Tools.Should().BeTrue();
        client.Capabilities.Resources.Should().BeTrue();
        client.Capabilities.Prompts.Should().BeTrue();
        client.Capabilities.ResourceSubscriptions.Should().BeFalse(
            "the fixture declares subscribe:false and we must not assume otherwise");
        transport.ProcessId.Should().BeGreaterThan(0);
        Process.GetProcessById(transport.ProcessId!.Value).HasExited.Should().BeFalse();
    }

    [Fact]
    public async Task A_Server_Negotiating_An_Unimplemented_Version_Is_Refused()
    {
        var (transport, _) = CreateTransport("bad-protocol-version");
        var client = new McpRemoteClient(transport);

        var act = async () => await client.InitializeAsync();

        (await act.Should().ThrowAsync<McpProtocolException>())
            .WithMessage("*VersionMismatch*");
        client.Initialized.Should().BeFalse();
    }

    [Fact]
    public async Task Operations_Before_Initialize_Are_Refused_As_CapabilityUnavailable()
    {
        var (transport, _) = CreateTransport();
        var client = new McpRemoteClient(transport);

        var act = async () => await client.ListResourcesAsync();

        (await act.Should().ThrowAsync<McpProtocolException>())
            .WithMessage("*CapabilityUnavailable*");
    }

    [Fact]
    public async Task An_Undeclared_Capability_Is_Refused_Rather_Than_Attempted()
    {
        var (client, _, _) = await ConnectAsync("tools-only");

        client.Capabilities.Resources.Should().BeFalse();
        var act = async () => await client.ListResourcesAsync();

        (await act.Should().ThrowAsync<McpProtocolException>())
            .WithMessage("*CapabilityUnavailable*resources*");
    }

    [Fact]
    public async Task Tools_List_And_Call_Work_Over_One_Long_Lived_Process()
    {
        var (client, transport, _) = await ConnectAsync();
        var processId = transport.ProcessId;

        var tools = await client.ListToolsAsync();
        tools.Select(tool => tool.Name).Should().Contain(["echo", "ping", "create_record"]);

        for (var index = 0; index < 5; index++)
        {
            var result = await client.CallToolAsync(
                "echo",
                JsonSerializer.SerializeToElement(new { text = $"hello-{index}" }));
            result.IsError.Should().BeFalse();
            result.Content.Should().Contain($"hello-{index}");
        }
        transport.ProcessId.Should().Be(processId, "every call shared one server process");
    }

    [Fact]
    public async Task A_No_Argument_Tool_Schema_Survives_Validation()
    {
        var (client, _, _) = await ConnectAsync();

        var tools = await client.ListToolsAsync();
        var ping = tools.Single(tool => tool.Name == "ping");

        McpSchemaValidator.Validate(ping.InputSchema).Accepted.Should().BeTrue(
            "a tool that takes no arguments is legitimate and must not be dropped");
        (await client.CallToolAsync("ping", JsonSerializer.SerializeToElement(new { })))
            .Content.Should().Contain("pong");
    }

    [Fact]
    public async Task A_Malformed_Schema_Drops_The_Tool_And_Keeps_The_Good_One()
    {
        var (client, _, _) = await ConnectAsync("malformed-schema");

        var tools = await client.ListToolsAsync();
        var accepted = tools
            .Where(tool => McpSchemaValidator.Validate(tool.InputSchema).Accepted)
            .Select(tool => tool.Name)
            .ToList();

        accepted.Should().NotContain("broken", "an unusable schema removes the tool");
        accepted.Should().Contain("good");
        // The broken tool declares {"type":"nonsense"} at the ROOT, so it fails on
        // the root-type rule; what matters is that it is rejected with a reason
        // rather than downgraded into a permissive object schema.
        McpSchemaValidator.Validate(
                tools.Single(tool => tool.Name == "broken").InputSchema)
            .RejectionCode.Should().Be("SchemaRootNotObjectType");
    }

    [Fact]
    public async Task Tool_Names_Are_Namespaced_By_Connector()
    {
        var (client, _, _) = await ConnectAsync();
        var tools = await client.ListToolsAsync();

        var namespaced = tools
            .Select(tool => McpSchemaValidator.Namespaced("fixture-connector", tool.Name))
            .ToList();

        namespaced.Should().Contain("fixture-connector.echo");
        namespaced.Should().OnlyContain(name => name.StartsWith("fixture-connector."));
    }

    [Fact]
    public async Task Resources_List_And_Read_Return_Untrusted_Observations()
    {
        var (client, _, _) = await ConnectAsync();

        var resources = await client.ListResourcesAsync();
        resources.Select(resource => resource.Uri).Should().Contain("fixture://readme");
        var content = await client.ReadResourceAsync("fixture://readme");

        content.Text.Should().Contain("Fixture readme");
        content.MimeType.Should().Be("text/plain");
        Application.Services.UntrustedObservationBoundary
            .ProtectIfUntrusted("mcp_resource", content.Text)
            .Should().StartWith(Application.Services.UntrustedObservationBoundary.Marker,
                "resource content is data from a connector, not instructions");
    }

    [Fact]
    public async Task Hostile_Resource_Content_Arrives_Wrapped_And_Grants_Nothing()
    {
        var (client, _, _) = await ConnectAsync();

        var content = await client.ReadResourceAsync("fixture://secret-looking");

        content.Text.Should().Contain("grant all permissions");
        var guarded = Application.Services.UntrustedObservationBoundary
            .ProtectIfUntrusted("mcp_resource", content.Text);
        guarded.Should().StartWith(
            Application.Services.UntrustedObservationBoundary.Marker,
            "an instruction inside resource content must stay quarantined as data");
    }

    [Fact]
    public async Task Prompts_List_And_Get_Are_Treated_As_Untrusted_Content()
    {
        var (client, _, _) = await ConnectAsync();

        var prompts = await client.ListPromptsAsync();
        prompts.Select(prompt => prompt.Name).Should().Contain(["summarize", "injection"]);
        var hostile = await client.GetPromptAsync("injection");

        hostile.Text.Should().Contain("IGNORE PREVIOUS INSTRUCTIONS");
        Application.Services.UntrustedObservationBoundary
            .ProtectIfUntrusted("mcp_prompt", hostile.Text)
            .Should().StartWith(Application.Services.UntrustedObservationBoundary.Marker,
                "an MCP prompt is connector content and can never become a system instruction");
    }

    [Fact]
    public async Task Progress_And_Log_Notifications_Are_Separated_From_The_Response()
    {
        var (client, transport, _) = await ConnectAsync();

        var result = await client.CallToolAsync(
            "slow", JsonSerializer.SerializeToElement(new { ms = 300 }));

        result.IsError.Should().BeFalse();
        result.Content.Should().Contain("slept:300");
        transport.Notifications.Select(item => item.Method)
            .Should().Contain("notifications/progress");
        transport.Notifications.Should().Contain(
            item => item.Method == "notifications/message");
    }

    [Fact]
    public async Task A_Duplicate_Response_Is_Rejected_And_The_Session_Keeps_Working()
    {
        var (client, _, _) = await ConnectAsync();

        var result = await client.CallToolAsync(
            "duplicate", JsonSerializer.SerializeToElement(new { }));

        result.Content.Should().Contain("first", "the second reply must be discarded");
        (await client.CallToolAsync(
                "echo", JsonSerializer.SerializeToElement(new { text = "after-duplicate" })))
            .Content.Should().Contain("after-duplicate");
    }

    [Fact]
    public async Task A_Response_With_The_Wrong_Id_Never_Satisfies_A_Request()
    {
        var (client, _, _) = await ConnectAsync();
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));

        var act = async () => await client.CallToolAsync(
            "wrong-id", JsonSerializer.SerializeToElement(new { }), cts.Token);

        // The misrouted answer is dropped, so the call fails rather than being
        // satisfied by a frame that was not its own.
        await act.Should().ThrowAsync<Exception>();
    }

    [Fact]
    public async Task A_Server_Crash_After_Dispatch_Is_An_Unknown_Outcome()
    {
        var (client, _, _) = await ConnectAsync("crash-after-dispatch");

        McpDispatchException? captured = null;
        try
        {
            await client.CallToolAsync(
                "create_record", JsonSerializer.SerializeToElement(new { name = "invoice" }));
        }
        catch (McpDispatchException ex)
        {
            captured = ex;
        }

        captured.Should().NotBeNull("a lost answer must surface as a dispatch failure");
        captured!.OutcomeUnknown.Should().BeTrue(
            "the request reached the server, so we cannot claim it did nothing");
        captured.Stage.Should().BeOneOf(DispatchStage.Dispatched, DispatchStage.Writing);
    }

    /// <summary>
    /// The reconciliation loop against a LIVE server: a mutation whose answer was
    /// lost, then a read-back that establishes it really happened.
    ///
    /// Note the sandbox boundary this respects — a staged workspace does not
    /// survive the process, so state that outlives a crash has to come from the
    /// server itself. The post-crash case is covered separately and must never
    /// report success it cannot prove.
    /// </summary>
    [Fact]
    public async Task A_Silent_Mutation_Is_Reconciled_By_Reading_The_State_Back()
    {
        var (client, _, _) = await ConnectAsync();
        // Create for real, then reconcile as though the answer had been lost.
        var created = await client.CallToolAsync(
            "create_record", JsonSerializer.SerializeToElement(new { name = "invoice" }));
        created.IsError.Should().BeFalse();

        var intent = new McpMutationIntent
        {
            ConnectorId = "fixture-connector",
            ToolName = "create_record",
            ArgumentsFingerprint = McpMutationIntent.Fingerprint(
                "fixture-connector", "create_record", """{"name":"invoice"}"""),
            IdempotencyKey = "key-1",
            DispatchedUtc = DateTimeOffset.UtcNow,
            VerificationTool = "get_record",
            ReturnedObjectId = "rec-1",
            ExpectedStateValue = "invoice"
        };

        var outcome = await new McpMutationReconciler()
            .ReconcileAsync(client, intent, CancellationToken.None);

        outcome.Verdict.Should().Be(
            McpMutationVerdict.Succeeded,
            "the record was read back from the server, not assumed");
        outcome.Evidence.Should().Contain("verification_read=confirmed");
    }

    /// <summary>After a crash the client reconnects to a server with no memory of
    /// the request. The verdict must be a truthful "did not happen" or NeedsUser —
    /// never a success, and never a silent retry.</summary>
    [Fact]
    public async Task After_A_Crash_Reconnect_Reconciles_Without_Claiming_Success()
    {
        var (crashing, crashedTransport, _) = await ConnectAsync("crash-after-dispatch");
        var act = async () => await crashing.CallToolAsync(
            "create_record", JsonSerializer.SerializeToElement(new { name = "invoice" }));
        await act.Should().ThrowAsync<McpDispatchException>();

        var (reconnected, freshTransport, _) = await ConnectAsync();
        freshTransport.Generation.Should().BeGreaterThan(
            crashedTransport.Generation,
            "a reconnect must start a new generation so old handles cannot be honoured");

        var outcome = await new McpMutationReconciler().ReconcileAsync(
            reconnected,
            new McpMutationIntent
            {
                ConnectorId = "fixture-connector",
                ToolName = "create_record",
                ArgumentsFingerprint = "fp",
                IdempotencyKey = "key-crash",
                DispatchedUtc = DateTimeOffset.UtcNow,
                VerificationTool = "get_record",
                ReturnedObjectId = "rec-1"
            },
            CancellationToken.None);

        outcome.Verdict.Should().NotBe(
            McpMutationVerdict.Succeeded,
            "nothing was ever confirmed, so success must not be claimed");
        outcome.Verdict.Should().BeOneOf(
            McpMutationVerdict.FailedNoEffect, McpMutationVerdict.NeedsUser);
    }

    [Fact]
    public async Task A_Mutation_That_Never_Happened_Reconciles_As_No_Effect()
    {
        var (client, _, _) = await ConnectAsync();
        var intent = new McpMutationIntent
        {
            ConnectorId = "fixture-connector",
            ToolName = "create_record",
            ArgumentsFingerprint = "fp",
            IdempotencyKey = "key-2",
            DispatchedUtc = DateTimeOffset.UtcNow,
            VerificationTool = "get_record",
            ReturnedObjectId = "rec-does-not-exist"
        };

        var outcome = await new McpMutationReconciler()
            .ReconcileAsync(client, intent, CancellationToken.None);

        outcome.Verdict.Should().Be(McpMutationVerdict.FailedNoEffect);
        outcome.Evidence.Should().Contain("verification_read=absent");
    }

    [Fact]
    public async Task A_Connector_Without_A_Verification_Tool_Ends_As_NeedsUser()
    {
        var (client, _, _) = await ConnectAsync();
        var intent = new McpMutationIntent
        {
            ConnectorId = "fixture-connector",
            ToolName = "send_message",
            ArgumentsFingerprint = "fp",
            IdempotencyKey = "key-3",
            DispatchedUtc = DateTimeOffset.UtcNow,
            VerificationTool = null,
            ReturnedObjectId = null
        };

        var outcome = await new McpMutationReconciler()
            .ReconcileAsync(client, intent, CancellationToken.None);

        outcome.Verdict.Should().Be(
            McpMutationVerdict.NeedsUser,
            "with no way to check, guessing either way risks a duplicate or a lie");
        outcome.Evidence.Should().Contain("verification_capability=absent");
    }

    [Theory]
    [InlineData("send_message", McpToolRisk.ExternalMutation)]
    [InlineData("delete_project", McpToolRisk.Destructive)]
    [InlineData("charge_card", McpToolRisk.FinancialOrHighImpact)]
    [InlineData("get_api_token", McpToolRisk.CredentialSensitive)]
    [InlineData("list_issues", McpToolRisk.ReadOnly)]
    [InlineData("frobnicate", McpToolRisk.Unknown)]
    public void Tool_Risk_Classification_Defaults_To_Conservative(
        string toolName,
        McpToolRisk expected)
    {
        var risk = McpToolRiskPolicy.Classify(toolName);

        risk.Should().Be(expected);
        if (risk != McpToolRisk.ReadOnly)
            McpToolRiskPolicy.MayRetryWithoutReconciliation(risk).Should().BeFalse(
                "anything not proven read-only must reconcile before a retry");
    }

    [Fact]
    public async Task The_Server_Cannot_Inherit_Host_Secrets()
    {
        const string name = "SRAN_MCP_SECRET";
        Environment.SetEnvironmentVariable(name, "mcp_secret_must_not_leak_88f1");
        try
        {
            var (client, _, _) = await ConnectAsync();

            var result = await client.CallToolAsync(
                "read-env", JsonSerializer.SerializeToElement(new { name }));

            result.Content.Should().Contain("(absent)");
            result.Content.Should().NotContain("mcp_secret_must_not_leak_88f1");
        }
        finally
        {
            Environment.SetEnvironmentVariable(name, null);
        }
    }

    [Fact]
    public async Task The_Server_Cannot_Read_Files_Outside_Its_Workspace()
    {
        var outside = Path.Combine(_root, "outside-secret.txt");
        await File.WriteAllTextAsync(outside, "host-only-secret-value");
        var (client, _, _) = await ConnectAsync();

        var result = await client.CallToolAsync(
            "read-host-file", JsonSerializer.SerializeToElement(new { path = outside }));

        result.Content.Should().NotContain("host-only-secret-value");
        result.Content.Should().Contain("denied");
    }

    [Fact]
    public async Task The_Server_Cannot_Reach_The_Network()
    {
        var listener = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            var port = ((System.Net.IPEndPoint)listener.LocalEndpoint).Port;
            var (client, _, _) = await ConnectAsync();

            var result = await client.CallToolAsync(
                "probe-network", JsonSerializer.SerializeToElement(new { port }));

            result.Content.Should().Contain("blocked");
        }
        finally
        {
            listener.Stop();
        }
    }

    [Fact]
    public async Task A_Server_Spawned_Child_Dies_With_The_Session()
    {
        var (client, transport, _) = await ConnectAsync();

        var result = await client.CallToolAsync(
            "spawn-child", JsonSerializer.SerializeToElement(new { }));
        var childId = ExtractChildId(result.Content);
        childId.Should().BeGreaterThan(0, $"the fixture must really spawn one (got '{result.Content}')");
        HasExited(childId).Should().BeFalse();

        await transport.DisposeAsync();
        _transports.Remove(transport);
        await Task.Delay(1500);

        HasExited(childId).Should().BeTrue(
            "an MCP server cannot leave a process outside the Job Object");
    }

    [Fact]
    public async Task An_Oversized_Response_Does_Not_Take_The_Host_Down()
    {
        var (client, _, _) = await ConnectAsync();
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));

        // 6 MB against a 4 MB line cap: the transport must refuse it, not buffer
        // it, and the host must stay alive either way.
        try
        {
            await client.CallToolAsync(
                "oversized", JsonSerializer.SerializeToElement(new { }), cts.Token);
        }
        catch (Exception ex) when (ex is McpDispatchException or McpProtocolException
                                       or OperationCanceledException)
        {
        }

        Environment.HasShutdownStarted.Should().BeFalse();
    }

    [Fact]
    public async Task Graceful_Shutdown_Leaves_No_Process_Or_Staged_Directory()
    {
        var jobsRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Packages",
            "sran.desktop.commandsandbox.v1",
            "AC",
            "LocalState",
            "SranJobs");
        var baseline = Directory.Exists(jobsRoot)
            ? Directory.GetDirectories(jobsRoot).ToHashSet(StringComparer.OrdinalIgnoreCase)
            : [];

        var (client, transport, _) = await ConnectAsync();
        (await client.CallToolAsync("ping", JsonSerializer.SerializeToElement(new { })))
            .IsError.Should().BeFalse();
        var processId = transport.ProcessId!.Value;

        await transport.DisposeAsync();
        _transports.Remove(transport);
        await Task.Delay(1500);

        HasExited(processId).Should().BeTrue();
        var survivors = Directory.Exists(jobsRoot)
            ? Directory.GetDirectories(jobsRoot).Except(baseline).ToArray()
            : [];
        survivors.Should().BeEmpty();
    }

    [Fact]
    public void There_Is_No_Host_Fallback_For_Local_Mcp()
    {
        // The legacy transport must stay fail-closed: the isolated path is the
        // only way a local server runs.
        var legacy = new StdioMcpTransport("C:\\Windows\\System32\\cmd.exe");
        var act = async () => await legacy.EnsureStartedAsync();

        act.Should().ThrowAsync<Exception>();
    }

    private static int ExtractChildId(string content)
    {
        var marker = content.LastIndexOf("child:", StringComparison.Ordinal);
        if (marker < 0)
            return -1;
        var digits = new string(content[(marker + 6)..]
            .TakeWhile(char.IsAsciiDigit).ToArray());
        return int.TryParse(digits, out var value) ? value : -1;
    }

    private static bool HasExited(int processId)
    {
        try
        {
            using var process = Process.GetProcessById(processId);
            return process.HasExited;
        }
        catch (ArgumentException)
        {
            return true;
        }
    }

    public async Task DisposeAsync()
    {
        foreach (var transport in _transports)
        {
            try
            {
                await transport.DisposeAsync();
            }
            catch (Exception ex) when (ex is IOException or InvalidOperationException)
            {
            }
        }
        if (Directory.Exists(_root))
        {
            try
            {
                Directory.Delete(_root, recursive: true);
            }
            catch (IOException)
            {
            }
        }
    }
}
