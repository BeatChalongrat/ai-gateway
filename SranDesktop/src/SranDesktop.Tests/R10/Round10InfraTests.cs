using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Remote;
using SranDesktop.Infrastructure.Speech;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.R10;

// ── Fix 1: MCP remote client over a scripted HTTP handler ───────────────────

public class McpRemoteClientTests
{
    private sealed class ScriptedHandler : HttpMessageHandler
    {
        public List<(string Method, string Body, string? SessionHeader)> Requests { get; } = new();
        private readonly Queue<HttpResponseMessage> _responses = new();

        public void Enqueue(string json, string? sessionId = null, string contentType = "application/json")
        {
            var response = new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(json, Encoding.UTF8, contentType)
            };
            if (sessionId is not null)
                response.Headers.Add("Mcp-Session-Id", sessionId);
            _responses.Enqueue(response);
        }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken ct)
        {
            var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct);
            request.Headers.TryGetValues("Mcp-Session-Id", out var session);
            Requests.Add((request.Method.Method, body, session?.FirstOrDefault()));
            return _responses.Count > 0
                ? _responses.Dequeue()
                : new HttpResponseMessage(HttpStatusCode.Accepted) { Content = new StringContent("") };
        }
    }

    private static (McpRemoteClient Client, ScriptedHandler Handler) Create()
    {
        var handler = new ScriptedHandler();
        var client = new McpRemoteClient("https://mcp.example.com/slack",
            authToken: "tok", http: new HttpClient(handler));
        return (client, handler);
    }

    [Fact]
    public async Task Initialize_Sends_Handshake_And_Tracks_Session()
    {
        var (client, handler) = Create();
        handler.Enqueue("""{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"slack-mcp"},"capabilities":{}}}""",
            sessionId: "sess-1");
        handler.Enqueue(""); // initialized notification ack

        var init = await client.InitializeAsync();

        Assert.Equal("slack-mcp", init.ServerName);
        Assert.Equal("sess-1", client.SessionId);
        Assert.Equal(2, handler.Requests.Count);
        Assert.Contains("\"initialize\"", handler.Requests[0].Body);
        Assert.Contains("notifications/initialized", handler.Requests[1].Body);
        // Session header rides on the follow-up request.
        Assert.Equal("sess-1", handler.Requests[1].SessionHeader);
        await client.DisposeAsync();
        // Dispose sends session DELETE.
        Assert.Equal("DELETE", handler.Requests[^1].Method);
    }

    [Fact]
    public async Task ListTools_Parses_Definitions()
    {
        var (client, handler) = Create();
        handler.Enqueue("""
            {"jsonrpc":"2.0","id":1,"result":{"tools":[
                {"name":"slack_send_message","description":"Send a message","inputSchema":{"type":"object","properties":{"channel":{"type":"string"}}}},
                {"name":"slack_search"}
            ]}}
            """);

        var tools = await client.ListToolsAsync();

        Assert.Equal(2, tools.Count);
        Assert.Equal("slack_send_message", tools[0].Name);
        Assert.Contains("channel", tools[0].InputSchema);
        Assert.Equal("slack_search", tools[1].Name);
    }

    [Fact]
    public async Task CallTool_Returns_Text_Content_And_Error_Flag()
    {
        var (client, handler) = Create();
        handler.Enqueue("""{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"sent!"}],"isError":false}}""");

        using var args = JsonDocument.Parse("""{"channel":"#general","text":"hi"}""");
        var result = await client.CallToolAsync("slack_send_message", args.RootElement);

        Assert.False(result.IsError);
        Assert.Equal("sent!", result.Content);
        Assert.Contains("tools/call", handler.Requests[0].Body);
        Assert.Contains("slack_send_message", handler.Requests[0].Body);
    }

    [Fact]
    public async Task CallTool_Surfaces_IsError()
    {
        var (client, handler) = Create();
        handler.Enqueue("""{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"channel not found"}],"isError":true}}""");

        using var args = JsonDocument.Parse("{}");
        var result = await client.CallToolAsync("slack_send_message", args.RootElement);

        Assert.True(result.IsError);
        Assert.Contains("channel not found", result.Content);
    }

    [Fact]
    public async Task Sse_Wrapped_Response_Is_Unwrapped()
    {
        var (client, handler) = Create();
        handler.Enqueue(
            "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"tools\":[]}}\n\n",
            contentType: "text/event-stream");

        var tools = await client.ListToolsAsync();
        Assert.Empty(tools);
    }

    [Fact]
    public void ExtractSseData_Takes_Last_Data_Line()
    {
        var body = "event: message\ndata: {\"a\":1}\n\nevent: message\ndata: {\"b\":2}\n\n";
        Assert.Equal("{\"b\":2}", McpRemoteClient.ExtractSseData(body));
    }
}

// ── Fix 3: branch persistence round-trip ────────────────────────────────────

public class BranchPersistenceTests : IDisposable
{
    private readonly string _dbPath = Path.Combine(Path.GetTempPath(), $"branch-test-{Guid.NewGuid():N}.db");

    [Fact]
    public async Task BranchSet_RoundTrips_And_Updates()
    {
        var store = new SqliteConversationStore(_dbPath);
        var record = new BranchSetRecord("conv-1", "msg-head", 1, new List<List<string>>
        {
            new() { "msg-head", "a1" },
            new() { "msg-head-2", "a2", "u3", "a3" }
        });

        await store.SaveBranchSetAsync(record);
        var loaded = await store.GetBranchSetsAsync("conv-1");

        var set = Assert.Single(loaded);
        Assert.Equal("msg-head", set.BranchKey);
        Assert.Equal(1, set.ActiveIndex);
        Assert.Equal(2, set.Tails.Count);
        Assert.Equal(new[] { "msg-head-2", "a2", "u3", "a3" }, set.Tails[1]);

        // Switching branches updates the same row.
        await store.SaveBranchSetAsync(record with { ActiveIndex = 0 });
        var reloaded = (await store.GetBranchSetsAsync("conv-1")).Single();
        Assert.Equal(0, reloaded.ActiveIndex);
    }

    [Fact]
    public async Task No_Branch_Rows_Returns_Empty()
    {
        var store = new SqliteConversationStore(_dbPath);
        Assert.Empty(await store.GetBranchSetsAsync("conv-without-branches"));
    }

    public void Dispose()
    {
        Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
        try { File.Delete(_dbPath); } catch (IOException) { }
    }
}

// ── Fix 2: speech backend selection ─────────────────────────────────────────

public class SpeechServiceSelectionTests
{
    private sealed class FakeCredentials : ICredentialStore
    {
        private readonly Dictionary<string, string> _values = new();
        public void Save(string key, string value) => _values[key] = value;
        public string? Load(string key) => _values.GetValueOrDefault(key);
        public void Delete(string key) => _values.Remove(key);
    }

    private static ISettingsService MakeSettings(string backend = "auto")
    {
        var path = Path.Combine(Path.GetTempPath(), $"speech-settings-{Guid.NewGuid():N}.json");
        var service = new JsonSettingsService(new UserSettings { SpeechBackend = backend }, path);
        return service;
    }

    [Fact]
    public void Without_Azure_Key_Falls_Back_To_Windows()
    {
        using var composite = new CompositeSpeechService(MakeSettings(), new FakeCredentials());
        Assert.Equal("windows", composite.ActiveBackend.Id);
        Assert.Single(composite.AvailableBackends);
    }

    [Fact]
    public void With_Azure_Key_Prefers_Azure()
    {
        var credentials = new FakeCredentials();
        credentials.Save("azure_speech_key", "fake-key");
        using var composite = new CompositeSpeechService(MakeSettings(), credentials);

        Assert.Equal("azure", composite.ActiveBackend.Id);
        Assert.Equal(2, composite.AvailableBackends.Count);
        Assert.Contains("th-TH", composite.SupportedLanguages);
        Assert.True(composite.ActiveBackend.SupportsThaiStt);
    }

    [Fact]
    public void Explicit_Windows_Setting_Overrides_Azure_Key()
    {
        var credentials = new FakeCredentials();
        credentials.Save("azure_speech_key", "fake-key");
        using var composite = new CompositeSpeechService(MakeSettings("windows"), credentials);

        Assert.Equal("windows", composite.ActiveBackend.Id);
    }

    [Fact]
    public void SwitchBackend_Round_Trips()
    {
        var credentials = new FakeCredentials();
        credentials.Save("azure_speech_key", "fake-key");
        using var composite = new CompositeSpeechService(MakeSettings(), credentials);

        composite.SwitchBackend("windows");
        Assert.Equal("windows", composite.ActiveBackend.Id);
        composite.SwitchBackend("azure");
        Assert.Equal("azure", composite.ActiveBackend.Id);
    }

    [Fact]
    public void ReloadConfiguration_Picks_Up_New_Key()
    {
        var credentials = new FakeCredentials();
        using var composite = new CompositeSpeechService(MakeSettings(), credentials);
        Assert.Equal("windows", composite.ActiveBackend.Id);

        credentials.Save("azure_speech_key", "fake-key");
        composite.ReloadConfiguration();
        Assert.Equal("azure", composite.ActiveBackend.Id);
    }
}
