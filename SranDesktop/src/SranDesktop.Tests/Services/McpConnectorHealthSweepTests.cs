using System.Diagnostics;
using System.Net;
using System.Text;
using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 10 D4: the MCP health sweep dials connectors in parallel
/// with a bounded per-dial timeout — one hung server must not serialize the
/// sweep (previously a single dead dial held it for the full 30s HTTP client
/// timeout, on the health-timer thread and the post-login initial dial).
/// Uses a real loopback HttpListener: "good" speaks minimal MCP; "hang"
/// accepts the request and never answers.</summary>
public sealed class McpConnectorHealthSweepTests : IAsyncLifetime
{
    private HttpListener? _listener;
    private string _baseUrl = "";
    private string _stateFile = "";
    private McpConnectorService? _svc;

    private readonly List<HttpListenerContext> _hungContexts = new();
    private Task? _serveLoop;

    public Task InitializeAsync()
    {
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

        // Two installed connectors; "hang" FIRST so the old serial sweep
        // provably blocks "good" behind it.
        _stateFile = Path.Combine(Path.GetTempPath(), $"sran-sweep-{Guid.NewGuid():N}.json");
        File.WriteAllText(_stateFile, """[{"id":"hang"},{"id":"good"}]""");

        _svc = new McpConnectorService(
            Mock.Of<ICredentialStore>(), NullLogger<McpConnectorService>.Instance,
            catalogUrl: $"{_baseUrl}catalog", stateFilePath: _stateFile,
            dialTimeout: TimeSpan.FromSeconds(2));
        return Task.CompletedTask;
    }

    private async Task ServeAsync()
    {
        while (_listener is { IsListening: true })
        {
            HttpListenerContext ctx;
            try
            {
                ctx = await _listener.GetContextAsync();
            }
            catch (Exception ex) when (ex is HttpListenerException or ObjectDisposedException)
            {
                return; // listener closed by DisposeAsync
            }
            _ = Task.Run(() => HandleAsync(ctx));
        }
    }

    private async Task HandleAsync(HttpListenerContext ctx)
    {
        var path = ctx.Request.Url!.AbsolutePath;

        if (path == "/catalog")
        {
            await WriteJsonAsync(ctx, JsonSerializer.Serialize(new
            {
                connectors = new object[]
                {
                    new { id = "hang", name = "Hang", description = "", category = "data",
                          auth_type = "none", server_url = $"{_baseUrl}hang", tools = Array.Empty<string>() },
                    new { id = "good", name = "Good", description = "", category = "data",
                          auth_type = "none", server_url = $"{_baseUrl}good", tools = Array.Empty<string>() },
                }
            }));
            return;
        }

        if (path.StartsWith("/hang", StringComparison.Ordinal))
        {
            // Simulated dead server: hold the connection open, never respond.
            lock (_hungContexts)
                _hungContexts.Add(ctx);
            return;
        }

        // Minimal MCP endpoint for "good": initialize / initialized / tools/list.
        string body;
        using (var reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
            body = await reader.ReadToEndAsync();

        if (body.Contains("notifications/initialized", StringComparison.Ordinal))
            await WriteJsonAsync(ctx, "{}");
        else if (body.Contains("\"initialize\"", StringComparison.Ordinal))
            await WriteJsonAsync(ctx,
                """{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"Good"},"capabilities":{}}}""");
        else // tools/list
            await WriteJsonAsync(ctx,
                """{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"good_tool","description":"d","inputSchema":{"type":"object"}}]}}""");
    }

    private static async Task WriteJsonAsync(HttpListenerContext ctx, string json)
    {
        var bytes = Encoding.UTF8.GetBytes(json);
        ctx.Response.ContentType = "application/json";
        ctx.Response.ContentLength64 = bytes.Length;
        await ctx.Response.OutputStream.WriteAsync(bytes);
        ctx.Response.Close();
    }

    [Fact]
    public async Task One_Hung_Connector_Does_Not_Stall_The_Sweep()
    {
        var statusChanges = 0;
        _svc!.StatusChanged += (_, _) => Interlocked.Increment(ref statusChanges);

        var sw = Stopwatch.StartNew();
        await _svc.RefreshRemoteToolsAsync();
        sw.Stop();

        // "good" resolves despite "hang" never answering, and the whole sweep
        // completes within the bounded per-dial timeout — not the 30s HTTP
        // client timeout the serial sweep was hostage to.
        _svc.GetStatus("good").Should().Be(ConnectorStatus.Connected);
        _svc.GetStatus("hang").Should().Be(ConnectorStatus.Disconnected);
        sw.Elapsed.Should().BeLessThan(TimeSpan.FromSeconds(15),
            "one hung server must not serialize the sweep");

        // StatusChanged semantics preserved: both transitions fired.
        statusChanges.Should().BeGreaterThanOrEqualTo(2);

        // The healthy connector's tools are live.
        _svc.GetCachedRemoteToolDefinitions()
            .Should().Contain(d => d.Function.Name == "good_tool");
    }

    public async Task DisposeAsync()
    {
        if (_svc is not null)
            await _svc.DisposeAsync();

        lock (_hungContexts)
        {
            foreach (var ctx in _hungContexts)
            {
                try { ctx.Response.Abort(); }
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

        try
        {
            if (File.Exists(_stateFile)) File.Delete(_stateFile);
        }
        catch (IOException) { }
    }
}
