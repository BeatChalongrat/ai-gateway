using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp;
using SranDesktop.Infrastructure.Mcp.Remote;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>Phase 12 D: transport selection + legacy connectors.json
/// compatibility. Existing HTTP connectors must be untouched by the stdio
/// feature: no transport field → http, and re-saving the state file must not
/// grow new keys.</summary>
public sealed class McpTransportSelectionTests : IDisposable
{
    private readonly string _stateFile = Path.Combine(
        Path.GetTempPath(), $"sran-sel-{Guid.NewGuid():N}.json");

    private McpConnectorService CreateService() => new(
        Mock.Of<ICredentialStore>(), NullLogger<McpConnectorService>.Instance,
        stateFilePath: _stateFile);

    [Fact]
    public async Task Stdio_Connector_Selects_Stdio_Transport()
    {
        await using var svc = CreateService();

        var transport = svc.CreateTransport(new McpConnector
        {
            Id = "local",
            Transport = McpConnector.TransportStdio,
            Command = "server.exe",
            Args = new List<string> { "--flag" }
        });

        transport.Should().BeOfType<StdioMcpTransport>();
        await transport.DisposeAsync();
    }

    [Fact]
    public async Task Legacy_Connector_Json_Without_Transport_Field_Selects_Http()
    {
        // A REAL pre-Phase-12 catalog entry — no "transport" key existed.
        const string legacyCatalogEntry = """
            {"id":"notion","name":"Notion","description":"Search and read pages",
             "category":"productivity","auth_type":"api_key",
             "server_url":"https://mcp.sran.ai/notion",
             "tools":["notion_search","notion_read_page"]}
            """;
        var connector = JsonSerializer.Deserialize<McpConnector>(
            legacyCatalogEntry, new JsonSerializerOptions(JsonSerializerDefaults.Web))!;

        connector.Transport.Should().Be(McpConnector.TransportHttp,
            "legacy connectors must keep their exact pre-Phase-12 behavior");
        connector.ServerUrl.Should().Be("https://mcp.sran.ai/notion");

        await using var svc = CreateService();
        var transport = svc.CreateTransport(connector);
        transport.Should().BeOfType<HttpMcpTransport>();
        await transport.DisposeAsync();
    }

    [Fact]
    public void Legacy_ConnectorsJson_Deserializes_And_Resaves_Without_New_Fields()
    {
        // A REAL pre-Phase-12 connectors.json: a Phase-7 record plus a
        // minimal legacy one (the shape live installs actually wrote).
        const string legacyStateJson = """
            [
              {
                "id": "notion",
                "installed_at": "2026-05-01T09:30:00+00:00",
                "has_credentials": true,
                "auth_mode": "api_key"
              },
              { "id": "jira" }
            ]
            """;

        var state = JsonSerializer.Deserialize<List<InstalledConnector>>(legacyStateJson)!;

        state.Should().HaveCount(2);
        state[0].Id.Should().Be("notion");
        state[0].HasCredentials.Should().BeTrue();
        state[0].AuthMode.Should().Be("api_key");
        state[0].InstalledAt.Should().Be(DateTimeOffset.Parse("2026-05-01T09:30:00+00:00"));
        state[1].Id.Should().Be("jira");
        state.Should().OnlyContain(r => r.Custom == null,
            "legacy records carry no custom definition");

        // Re-saving with the service's serializer options must not grow keys:
        // pre-existing installs stay byte-compatible on disk.
        var resaved = JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true });
        resaved.Should().NotContain("custom").And.NotContain("transport");

        var again = JsonSerializer.Deserialize<List<InstalledConnector>>(resaved)!;
        again[0].InstalledAt.Should().Be(state[0].InstalledAt);
        again[0].AuthMode.Should().Be("api_key");
        again[1].AuthMode.Should().BeNull();
    }

    public void Dispose()
    {
        try
        {
            if (File.Exists(_stateFile)) File.Delete(_stateFile);
        }
        catch (IOException) { }
    }
}

/// <summary>Phase 12 D: end-to-end stdio connectors through
/// McpConnectorService with a real child process — connect, cache, execute,
/// persist, crash-surface, and consent-gate.</summary>
public sealed class StdioConnectorServiceTests : IDisposable
{
    private static readonly TimeSpan TestTimeout = TimeSpan.FromSeconds(30);

    private readonly string _stateFile = Path.Combine(
        Path.GetTempPath(), $"sran-stdio-{Guid.NewGuid():N}.json");

    private McpConnectorService CreateService() => new(
        Mock.Of<ICredentialStore>(), NullLogger<McpConnectorService>.Instance,
        stateFilePath: _stateFile);

    private static McpConnector EchoConnector(string id = "local-echo")
    {
        var (command, args) = StdioEchoServer.CommandLine();
        return new McpConnector
        {
            Id = id,
            Name = "Local Echo",
            Transport = McpConnector.TransportStdio,
            Command = command,
            Args = args
        };
    }

    [Fact]
    public async Task InstallCustom_Stdio_Connects_Caches_Tools_And_Persists()
    {
        await using var svc = CreateService();

        await svc.InstallCustomAsync(EchoConnector());

        svc.GetStatus("local-echo").Should().Be(ConnectorStatus.Connected);
        svc.GetCachedRemoteToolDefinitions()
            .Should().Contain(d => d.Function.Name == "stdio_echo");

        // Full definition persisted in connectors.json.
        var state = JsonSerializer.Deserialize<List<InstalledConnector>>(
            File.ReadAllText(_stateFile))!;
        var record = state.Single(c => c.Id == "local-echo");
        record.Custom.Should().NotBeNull();
        record.Custom!.Transport.Should().Be(McpConnector.TransportStdio);
        record.Custom.Command.Should().Be("powershell.exe");
        record.Custom.Args.Should().NotBeNullOrEmpty();

        // Tool executes end-to-end through the service.
        using var args = JsonDocument.Parse("""{"text":"via service"}""");
        var result = await svc.ExecuteRemoteToolAsync("stdio_echo", args.RootElement)
            .WaitAsync(TestTimeout);
        result.Success.Should().BeTrue();
        result.Content.Should().Be("via service");
    }

    [Fact]
    public async Task Persisted_Stdio_Connector_Redials_On_A_Fresh_Service()
    {
        await using (var first = CreateService())
        {
            await first.InstallCustomAsync(EchoConnector());
        }

        // New service instance = app restart: state comes purely from
        // connectors.json.
        await using var second = CreateService();
        await second.RefreshRemoteToolsAsync().WaitAsync(TestTimeout);

        second.GetStatus("local-echo").Should().Be(ConnectorStatus.Connected);
        second.GetCachedRemoteToolDefinitions()
            .Should().Contain(d => d.Function.Name == "stdio_echo");
    }

    [Fact]
    public async Task Stdio_Crash_During_Call_Surfaces_Disconnected_Via_StatusChanged()
    {
        await using var svc = CreateService();
        await svc.InstallCustomAsync(EchoConnector());

        var sawDisconnected = new TaskCompletionSource(
            TaskCreationOptions.RunContinuationsAsynchronously);
        svc.StatusChanged += (_, _) =>
        {
            if (svc.GetStatus("local-echo") == ConnectorStatus.Disconnected)
                sawDisconnected.TrySetResult();
        };

        // stdio_crash exits WITHOUT replying.
        using var args = JsonDocument.Parse("{}");
        var result = await svc.ExecuteRemoteToolAsync("stdio_crash", args.RootElement)
            .WaitAsync(TestTimeout);

        result.Success.Should().BeFalse();
        await sawDisconnected.Task.WaitAsync(TestTimeout);
        svc.GetStatus("local-echo").Should().Be(ConnectorStatus.Disconnected);
    }

    [Fact]
    public async Task Idle_Stdio_Exit_Surfaces_Disconnected_Via_The_Exited_Event()
    {
        await using var svc = CreateService();
        await svc.InstallCustomAsync(EchoConnector());

        var sawDisconnected = new TaskCompletionSource(
            TaskCreationOptions.RunContinuationsAsynchronously);
        svc.StatusChanged += (_, _) =>
        {
            if (svc.GetStatus("local-echo") == ConnectorStatus.Disconnected)
                sawDisconnected.TrySetResult();
        };

        // stdio_bye REPLIES (call succeeds → Connected), then the server
        // exits on its own 300ms later — so the Disconnected transition can
        // only come from the transport's Exited event, not a failed call.
        using var args = JsonDocument.Parse("{}");
        var result = await svc.ExecuteRemoteToolAsync("stdio_bye", args.RootElement)
            .WaitAsync(TestTimeout);
        result.Success.Should().BeTrue();

        await sawDisconnected.Task.WaitAsync(TestTimeout);
        svc.GetStatus("local-echo").Should().Be(ConnectorStatus.Disconnected);
    }

    [Fact]
    public async Task Stdio_Tools_Are_Consent_Gated_As_Ask()
    {
        await using var svc = CreateService();
        await svc.InstallCustomAsync(EchoConnector());

        // Same resolution path the orchestrator uses for every remote tool.
        var host = new McpToolHost(new McpToolRegistry(), svc);
        var tool = host.Resolve("stdio_echo");

        tool.Should().BeOfType<RemoteMcpTool>();
        tool!.DefaultPermission.Should().Be(PermissionLevel.Ask,
            "a stdio server is arbitrary local code — its tools must never auto-run");

        // And the permission service turns that into a consent prompt when
        // the user has no explicit override.
        var store = new Mock<IConversationStore>();
        store.Setup(s => s.GetToolPermissionAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string?)null);
        var permissions = new PermissionService(store.Object);

        var decision = await permissions.CheckAsync(tool, "scope");
        decision.Should().Be(PermissionDecision.NeedsConsent);
    }

    public void Dispose()
    {
        try
        {
            if (File.Exists(_stateFile)) File.Delete(_stateFile);
        }
        catch (IOException) { }
    }
}
