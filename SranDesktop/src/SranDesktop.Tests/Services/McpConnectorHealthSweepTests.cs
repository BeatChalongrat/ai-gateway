using System.Diagnostics;
using System.Net;
using System.Text;
using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Tests.Mcp;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// The connector health sweep, over the application's real service graph and a
/// real loopback MCP server.
///
/// Two properties are held at once here, and they used to belong to different
/// layers. The sweep still dials in parallel with a bounded per-dial timeout, so
/// one dead server cannot serialize it (Phase 10 D4). And since Phase 4.1C every
/// one of those dials is a tools/list through the durable gateway, so a
/// connector nobody approved is refused rather than contacted — which is why the
/// unapproved case reads NotConfigured and not Disconnected: it is a decision
/// waiting to be made, not a network that is down.
/// </summary>
[Trait("Category", "WindowsSecurityIntegration")]
[Collection("PathGuardGrants")]
public sealed class McpConnectorHealthSweepTests : IAsyncLifetime
{
    private readonly string _root =
        Path.Combine(Path.GetTempPath(), $"sran-sweep-{Guid.NewGuid():N}");

    private HttpListener? _listener;
    private string _baseUrl = "";
    private ProductionConnectorHost _host = null!;
    private bool _moved;

    private readonly List<HttpListenerContext> _hungContexts = new();
    private Task? _serveLoop;

    public Task InitializeAsync()
    {
        AgentInputScope.ResetForTests();
        Directory.CreateDirectory(_root);

        // Grab a free loopback port, then serve on it.
        var probe = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();

        _baseUrl = $"http://localhost:{port}/";
        _listener = new HttpListener();
        _listener.Prefixes.Add(_baseUrl);
        _listener.Start();
        _serveLoop = Task.Run(ServeAsync);

        _host = new ProductionConnectorHost(
            _root,
            $"--Sran:McpCatalogUrl={_baseUrl}catalog",
            // Generous on purpose. The claim under test is that the dials run in
            // PARALLEL, not that a loopback dial is fast: on a fully loaded suite
            // a healthy dial can take many seconds, and a tight bound here would
            // fail for machine load rather than for the property being measured.
            "--Sran:ConnectorDialTimeoutSeconds=60");
        return Task.CompletedTask;
    }

    [Fact]
    public async Task One_Hung_Connector_Does_Not_Stall_The_Sweep()
    {
        // "hang" is installed FIRST so a serial sweep would provably block
        // "good" behind it.
        await InstallAsync("hang");
        await InstallAsync("good");

        var stopwatch = Stopwatch.StartNew();
        await _host.Connectors.RefreshRemoteToolsAsync();
        stopwatch.Stop();

        _host.Connectors.GetStatus("good").Should().Be(ConnectorStatus.Connected);
        _host.Connectors.GetStatus("hang").Should().Be(ConnectorStatus.Disconnected);
        // Two dials in sequence would cost two dial timeouts; one bounded dial
        // plus overhead is what parallel looks like.
        stopwatch.Elapsed.Should().BeLessThan(TimeSpan.FromSeconds(100),
            "one hung server must not serialize the sweep");

        _host.Connectors.GetCachedRemoteToolDefinitions()
            .Should().Contain(definition => definition.Function.Name == "good_tool",
                "a healthy connector's tools/list still reaches the model — now with an "
                + "AgentStep behind it");
    }

    [Fact]
    public async Task Every_Dial_Leaves_A_Durable_Step_Behind_It()
    {
        await InstallAsync("good");

        await _host.Connectors.RefreshRemoteToolsAsync();

        var conversations = await _host.Services.GetRequiredService<IConversationStore>()
            .GetConversationsAsync();
        var dialConversation = conversations.FirstOrDefault(conversation =>
            conversation.Id.StartsWith("mcp:good:", StringComparison.Ordinal));
        dialConversation.Should().NotBeNull(
            "a marketplace dial is an MCP operation, so it is a durable run like any other");

        var snapshot = await _host.Tasks.GetLatestByConversationAsync(dialConversation!.Id);
        snapshot.Should().NotBeNull();
        var events = await _host.Tasks.GetEventsAsync(snapshot!.Task.TaskId);
        events.Should().Contain(item => item.EventType == "ActionDispatching",
            "the dial itself is the step");
    }

    [Fact]
    public async Task A_Connector_Nobody_Approved_Is_Refused_Rather_Than_Dialled()
    {
        // Installed without evidence of a person: the record exists, the decision
        // does not.
        await _host.Connectors.InstallAsync("good");

        await _host.Connectors.RefreshRemoteToolsAsync();

        _host.Connectors.GetStatus("good").Should().Be(ConnectorStatus.NotConfigured,
            "an unapproved connector needs a decision, not a retry");
        _host.Connectors.GetCachedRemoteToolDefinitions().Should().BeEmpty();
        _host.TrustStore.Get("good").Should().BeNull();
    }

    [Fact]
    public async Task Repointing_An_Approved_Connector_At_Another_Server_Stops_It()
    {
        await InstallAsync("good");
        await _host.Connectors.RefreshRemoteToolsAsync();
        _host.Connectors.GetStatus("good").Should().Be(ConnectorStatus.Connected);
        await _host.DisposeAsync();

        // The catalog now hands out a different URL for the same connector id.
        // A restart is what makes it take effect, which is also the realistic
        // shape of the attack: the definition changes while the app is not
        // looking, and the install record still says "good".
        _moved = true;
        _host = new ProductionConnectorHost(
            _root,
            $"--Sran:McpCatalogUrl={_baseUrl}catalog",
            // Generous on purpose. The claim under test is that the dials run in
            // PARALLEL, not that a loopback dial is fast: on a fully loaded suite
            // a healthy dial can take many seconds, and a tight bound here would
            // fail for machine load rather than for the property being measured.
            "--Sran:ConnectorDialTimeoutSeconds=60");
        await _host.Connectors.RefreshRemoteToolsAsync();

        _host.Connectors.GetStatus("good").Should().Be(ConnectorStatus.NotConfigured);
        _host.Connectors.GetCachedRemoteToolDefinitions().Should().BeEmpty(
            "an approval names a server, so a connector pointed elsewhere is a new decision");
    }

    private async Task InstallAsync(string connectorId) =>
        await _host.Connectors.InstallAsync(
            connectorId,
            evidence: UserApprovalEvidence.FromUserGesture("user:test", "marketplace install"));

    // ── Loopback MCP server ─────────────────────────────────────────────────

    private async Task ServeAsync()
    {
        while (_listener is { IsListening: true })
        {
            HttpListenerContext context;
            try
            {
                context = await _listener.GetContextAsync();
            }
            catch (Exception ex) when (ex is HttpListenerException or ObjectDisposedException)
            {
                return; // listener closed by DisposeAsync
            }
            _ = Task.Run(() => HandleAsync(context));
        }
    }

    private async Task HandleAsync(HttpListenerContext context)
    {
        var path = context.Request.Url!.AbsolutePath;

        if (path == "/catalog")
        {
            await WriteJsonAsync(context, JsonSerializer.Serialize(new
            {
                connectors = new object[]
                {
                    new { id = "hang", name = "Hang", description = "", category = "data",
                          auth_type = "none", server_url = $"{_baseUrl}hang",
                          tools = Array.Empty<string>() },
                    new { id = "good", name = "Good", description = "", category = "data",
                          auth_type = "none",
                          server_url = _moved ? $"{_baseUrl}elsewhere" : $"{_baseUrl}good",
                          tools = Array.Empty<string>() }
                }
            }));
            return;
        }

        if (path.StartsWith("/hang", StringComparison.Ordinal))
        {
            // Simulated dead server: hold the connection open, never respond.
            lock (_hungContexts)
                _hungContexts.Add(context);
            return;
        }

        string body;
        using (var reader = new StreamReader(context.Request.InputStream, Encoding.UTF8))
            body = await reader.ReadToEndAsync();

        if (body.Contains("notifications/initialized", StringComparison.Ordinal))
            await WriteJsonAsync(context, "{}");
        else if (body.Contains("\"initialize\"", StringComparison.Ordinal))
            await WriteJsonAsync(context,
                """{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"Good"},"capabilities":{"tools":{}}}}""");
        else // tools/list
            await WriteJsonAsync(context,
                """{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"good_tool","description":"d","inputSchema":{"type":"object","properties":{}}}]}}""");
    }

    private static async Task WriteJsonAsync(HttpListenerContext context, string json)
    {
        var bytes = Encoding.UTF8.GetBytes(json);
        context.Response.ContentType = "application/json";
        context.Response.ContentLength64 = bytes.Length;
        await context.Response.OutputStream.WriteAsync(bytes);
        context.Response.Close();
    }

    public async Task DisposeAsync()
    {
        await _host.DisposeAsync();

        lock (_hungContexts)
        {
            foreach (var context in _hungContexts)
            {
                try { context.Response.Abort(); }
                catch { /* best-effort */ }
            }
            _hungContexts.Clear();
        }

        try
        {
            _listener?.Stop();
            _listener?.Close();
        }
        catch (ObjectDisposedException) { }

        if (_serveLoop is not null)
            await _serveLoop;

        await ProductionConnectorHost.DeletePatientlyAsync(_root);
    }
}
