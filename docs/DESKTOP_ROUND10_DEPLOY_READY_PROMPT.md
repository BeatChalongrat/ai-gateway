# SRAN Desktop — Round 10: Deploy-Ready Infrastructure Prep

## Goal

Prepare all 3 remaining limitations so they "just work" at deploy time with minimal config. No new UI needed — only backend plumbing, abstraction layers, and local persistence.

---

## Fix 1 — MCP Connector: Real Protocol Client

### Problem
Current `McpConnectorService` has a placeholder `ExecuteRemoteToolAsync` that doesn't actually speak MCP protocol. When real MCP servers are deployed, it won't connect.

### What to build

Create a proper MCP client that speaks **JSON-RPC 2.0 over SSE** (the standard MCP transport for remote servers). When a connector is installed and its `server_url` is set, tool calls go through this client.

#### 1A. Create `Infrastructure/Mcp/Remote/McpRemoteClient.cs`

```csharp
using System.Net.Http;
using System.Text.Json;

namespace SranDesktop.Infrastructure.Mcp.Remote;

/// <summary>
/// Speaks MCP protocol (JSON-RPC 2.0 over HTTP+SSE) to a remote MCP server.
/// Spec: https://modelcontextprotocol.io/specification
/// </summary>
public class McpRemoteClient : IAsyncDisposable
{
    private readonly HttpClient _http;
    private readonly string _baseUrl;
    private readonly string? _authToken;
    private string? _sessionId;

    public McpRemoteClient(string baseUrl, string? authToken = null)
    {
        _baseUrl = baseUrl.TrimEnd('/');
        _authToken = authToken;
        _http = new HttpClient();
        if (authToken is not null)
            _http.DefaultRequestHeaders.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", authToken);
    }

    /// <summary>
    /// Initialize the MCP session. Call once after construction.
    /// POST {baseUrl}/mcp  with method: "initialize"
    /// Server responds with capabilities + session ID.
    /// </summary>
    public async Task<McpInitResult> InitializeAsync(CancellationToken ct = default)
    {
        var request = new JsonRpcRequest
        {
            Method = "initialize",
            Params = new
            {
                protocolVersion = "2025-06-18",
                capabilities = new { },
                clientInfo = new { name = "SranDesktop", version = "1.0.0" }
            }
        };

        var response = await PostRpcAsync(request, ct);
        _sessionId = response.SessionId;

        // Send initialized notification
        await PostRpcAsync(new JsonRpcRequest
        {
            Method = "notifications/initialized",
            IsNotification = true
        }, ct);

        return new McpInitResult
        {
            ServerName = response.Result?["serverInfo"]?["name"]?.ToString() ?? "Unknown",
            Tools = ParseTools(response.Result?["capabilities"]?["tools"])
        };
    }

    /// <summary>
    /// List available tools from the server.
    /// </summary>
    public async Task<List<McpToolDefinition>> ListToolsAsync(CancellationToken ct = default)
    {
        var response = await PostRpcAsync(new JsonRpcRequest { Method = "tools/list" }, ct);
        return ParseToolList(response.Result?["tools"]);
    }

    /// <summary>
    /// Execute a tool call on the remote server.
    /// </summary>
    public async Task<McpToolResult> CallToolAsync(
        string toolName,
        Dictionary<string, object?> arguments,
        CancellationToken ct = default)
    {
        var response = await PostRpcAsync(new JsonRpcRequest
        {
            Method = "tools/call",
            Params = new { name = toolName, arguments }
        }, ct);

        // Parse MCP tool result format
        var content = response.Result?["content"];
        var isError = response.Result?["isError"]?.GetValue<bool>() ?? false;

        return new McpToolResult
        {
            Content = content?.ToString() ?? "",
            IsError = isError
        };
    }

    /// <summary>
    /// Core JSON-RPC POST. Handles session headers.
    /// </summary>
    private async Task<JsonRpcResponse> PostRpcAsync(JsonRpcRequest request, CancellationToken ct)
    {
        var payload = new
        {
            jsonrpc = "2.0",
            id = request.IsNotification ? null : (object)Guid.NewGuid().ToString(),
            method = request.Method,
            @params = request.Params
        };

        var json = JsonSerializer.Serialize(payload,
            new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase });

        using var httpReq = new HttpRequestMessage(HttpMethod.Post, $"{_baseUrl}/mcp")
        {
            Content = new StringContent(json, System.Text.Encoding.UTF8, "application/json")
        };

        // Include session ID if we have one
        if (_sessionId is not null)
            httpReq.Headers.Add("Mcp-Session-Id", _sessionId);

        using var httpRes = await _http.SendAsync(httpReq, ct);
        httpRes.EnsureSuccessStatusCode();

        var responseJson = await httpRes.Content.ReadAsStringAsync(ct);
        var doc = JsonDocument.Parse(responseJson);

        // Extract session ID from response headers if present
        if (httpRes.Headers.TryGetValues("Mcp-Session-Id", out var sessionValues))
            _sessionId = sessionValues.FirstOrDefault();

        return new JsonRpcResponse
        {
            Result = doc.RootElement.TryGetProperty("result", out var r) ? r : null,
            Error = doc.RootElement.TryGetProperty("error", out var e) ? e : null,
            SessionId = _sessionId
        };
    }

    private static List<McpToolDefinition> ParseToolList(JsonElement? toolsElement)
    {
        var tools = new List<McpToolDefinition>();
        if (toolsElement is null) return tools;

        foreach (var tool in toolsElement.Value.EnumerateArray())
        {
            tools.Add(new McpToolDefinition
            {
                Name = tool.GetProperty("name").GetString() ?? "",
                Description = tool.TryGetProperty("description", out var d) ? d.GetString() : null,
                InputSchema = tool.TryGetProperty("inputSchema", out var s) ? s.ToString() : "{}"
            });
        }
        return tools;
    }

    private static List<McpToolDefinition> ParseTools(JsonElement? capTools) => new(); // filled by ListToolsAsync

    public async ValueTask DisposeAsync()
    {
        // Send session close if we have a session
        if (_sessionId is not null)
        {
            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Delete, $"{_baseUrl}/mcp");
                req.Headers.Add("Mcp-Session-Id", _sessionId);
                await _http.SendAsync(req);
            }
            catch { /* best effort */ }
        }
        _http.Dispose();
    }
}

// ── Supporting types ──

public class JsonRpcRequest
{
    public string Method { get; set; } = "";
    public object? Params { get; set; }
    public bool IsNotification { get; set; }
}

public class JsonRpcResponse
{
    public JsonElement? Result { get; set; }
    public JsonElement? Error { get; set; }
    public string? SessionId { get; set; }
}

public class McpInitResult
{
    public string ServerName { get; set; } = "";
    public List<McpToolDefinition> Tools { get; set; } = new();
}

public class McpToolDefinition
{
    public string Name { get; set; } = "";
    public string? Description { get; set; }
    public string InputSchema { get; set; } = "{}";
}

public class McpToolResult
{
    public string Content { get; set; } = "";
    public bool IsError { get; set; }
}
```

#### 1B. Integrate into `McpConnectorService`

Replace the placeholder `ExecuteRemoteToolAsync` with real calls:

```csharp
// Cache active clients per connector
private readonly Dictionary<string, McpRemoteClient> _clients = new();

public async Task<McpRemoteClient> GetOrCreateClientAsync(ConnectorConfig connector, CancellationToken ct)
{
    if (_clients.TryGetValue(connector.Id, out var existing))
        return existing;

    var token = await _credentialStore.GetAsync($"connector_{connector.Id}");
    var client = new McpRemoteClient(connector.ServerUrl, token);
    await client.InitializeAsync(ct);
    _clients[connector.Id] = client;
    return client;
}

public async Task<string> ExecuteRemoteToolAsync(
    string connectorId, string toolName,
    Dictionary<string, object?> args, CancellationToken ct)
{
    var connector = GetInstalledConnector(connectorId);
    var client = await GetOrCreateClientAsync(connector, ct);
    var result = await client.CallToolAsync(toolName, args, ct);

    if (result.IsError)
        throw new McpToolExecutionException(toolName, result.Content);

    return result.Content;
}
```

#### 1C. Register remote tools in ChatOrchestrator

When building tool definitions for the gateway request, include tools from installed connectors:

```csharp
// In ChatOrchestrator.RunAsync, when building tools:
var localTools = _toolHost.GetToolDefinitions();
var remoteTools = await _connectorService.GetAllInstalledToolDefinitionsAsync(ct);
var allTools = localTools.Concat(remoteTools).ToList();
```

Each remote tool definition should be prefixed with the connector name to avoid collisions:
`slack_send_message`, `gmail_search`, `gdrive_list_files`, etc.

#### 1D. Config file for deployment

Create `appsettings.connectors.json` (or use existing settings infrastructure):

```json
{
  "McpConnectors": {
    "CatalogUrl": "https://mcp.sran.ai/catalog.json",
    "FallbackCatalog": "embedded",
    "DefaultTransport": "streamable-http",
    "TimeoutSeconds": 30,
    "MaxRetries": 2
  }
}
```

At deploy time: just change `CatalogUrl` to point to the real catalog, and deploy MCP servers. Desktop app picks up the catalog and everything works.

#### 1E. Connection health check

Add a periodic health check (every 5 minutes) for connected MCP servers:

```csharp
public async Task<bool> PingAsync(string connectorId, CancellationToken ct)
{
    try
    {
        var client = await GetOrCreateClientAsync(GetInstalledConnector(connectorId), ct);
        await client.ListToolsAsync(ct);
        return true;
    }
    catch
    {
        _clients.Remove(connectorId);
        return false;
    }
}
```

Show connection status in the Connectors UI: green dot = connected, red dot = disconnected, grey = not configured.

---

## Fix 2 — Thai Speech via Abstracted Speech Service

### Problem
`System.Speech.Recognition` doesn't support Thai. Current `VoiceService` is hardcoded to use it.

### What to build

Abstract the speech service so it can use multiple backends. Ship with both Windows built-in AND Azure Speech SDK. At deploy time: just add an Azure key in settings.

#### 2A. Create `Application/Abstractions/ISpeechService.cs`

```csharp
namespace SranDesktop.Application.Abstractions;

public interface ISpeechService
{
    /// <summary>Available speech-to-text backends on this machine.</summary>
    IReadOnlyList<SpeechBackend> AvailableBackends { get; }

    /// <summary>Current active backend.</summary>
    SpeechBackend ActiveBackend { get; }

    /// <summary>Languages supported by the active backend.</summary>
    IReadOnlyList<string> SupportedLanguages { get; }

    /// <summary>Start listening. Raises TextRecognized events.</summary>
    Task StartRecognitionAsync(string language, CancellationToken ct = default);

    /// <summary>Stop listening.</summary>
    Task StopRecognitionAsync();

    /// <summary>Synthesize text to audio and play it.</summary>
    Task SpeakAsync(string text, string? voiceName = null, double speed = 1.0, CancellationToken ct = default);

    /// <summary>Stop any ongoing speech.</summary>
    void StopSpeaking();

    /// <summary>List available TTS voices for current backend.</summary>
    IReadOnlyList<VoiceInfo> GetVoices(string? language = null);

    /// <summary>Fired when speech is recognized (partial or final).</summary>
    event EventHandler<SpeechRecognizedEventArgs> TextRecognized;

    /// <summary>Fired when listening status changes.</summary>
    event EventHandler<bool> ListeningChanged;
}

public record SpeechBackend(string Id, string DisplayName, bool SupportsThaiStt, bool SupportsThaiTts);
public record VoiceInfo(string Id, string Name, string Language, string Gender);
public record SpeechRecognizedEventArgs(string Text, bool IsFinal);
```

#### 2B. Create `Infrastructure/Speech/WindowsSpeechService.cs`

Wraps the existing `System.Speech` logic. Only supports English and other Windows-installed language packs.

```csharp
public class WindowsSpeechService : ISpeechService
{
    public IReadOnlyList<SpeechBackend> AvailableBackends => new[]
    {
        new SpeechBackend("windows", "Windows Built-in", false, false)
    };

    // ... wrap existing SpeechRecognitionEngine + SpeechSynthesizer code ...
    // SupportedLanguages: enumerate from InstalledRecognizers
}
```

#### 2C. Create `Infrastructure/Speech/AzureSpeechService.cs`

```csharp
using Microsoft.CognitiveServices.Speech;
using Microsoft.CognitiveServices.Speech.Audio;

public class AzureSpeechService : ISpeechService
{
    private readonly string _subscriptionKey;
    private readonly string _region;
    private SpeechRecognizer? _recognizer;
    private SpeechSynthesizer? _synthesizer;

    public AzureSpeechService(string subscriptionKey, string region)
    {
        _subscriptionKey = subscriptionKey;
        _region = region;
    }

    public IReadOnlyList<SpeechBackend> AvailableBackends => new[]
    {
        new SpeechBackend("azure", "Azure AI Speech", true, true)
    };

    public IReadOnlyList<string> SupportedLanguages => new[]
    {
        "th-TH", "en-US", "en-GB", "ja-JP", "zh-CN", "ko-KR",
        "fr-FR", "de-DE", "es-ES", "pt-BR", "vi-VN", "id-ID"
        // Azure supports 100+ locales
    };

    public async Task StartRecognitionAsync(string language, CancellationToken ct)
    {
        var config = SpeechConfig.FromSubscription(_subscriptionKey, _region);
        config.SpeechRecognitionLanguage = language; // "th-TH" for Thai
        config.SetProperty(PropertyId.SpeechServiceResponse_PostProcessingOption, "TrueText");

        var audioConfig = AudioConfig.FromDefaultMicrophoneInput();
        _recognizer = new SpeechRecognizer(config, audioConfig);

        _recognizer.Recognizing += (s, e) =>
            TextRecognized?.Invoke(this, new SpeechRecognizedEventArgs(e.Result.Text, false));

        _recognizer.Recognized += (s, e) =>
        {
            if (e.Result.Reason == ResultReason.RecognizedSpeech)
                TextRecognized?.Invoke(this, new SpeechRecognizedEventArgs(e.Result.Text, true));
        };

        await _recognizer.StartContinuousRecognitionAsync();
        ListeningChanged?.Invoke(this, true);
    }

    public async Task StopRecognitionAsync()
    {
        if (_recognizer is not null)
        {
            await _recognizer.StopContinuousRecognitionAsync();
            _recognizer.Dispose();
            _recognizer = null;
        }
        ListeningChanged?.Invoke(this, false);
    }

    public async Task SpeakAsync(string text, string? voiceName, double speed, CancellationToken ct)
    {
        var config = SpeechConfig.FromSubscription(_subscriptionKey, _region);

        // Thai TTS voice
        config.SpeechSynthesisVoiceName = voiceName ?? "th-TH-PremwadeeNeural";

        // Speed via SSML
        var ssml = $@"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='th-TH'>
            <voice name='{config.SpeechSynthesisVoiceName}'>
                <prosody rate='{speed:F1}'>{System.Security.SecurityElement.Escape(text)}</prosody>
            </voice>
        </speak>";

        var audioConfig = AudioConfig.FromDefaultSpeakerOutput();
        _synthesizer = new SpeechSynthesizer(config, audioConfig);

        await _synthesizer.SpeakSsmlAsync(ssml);
    }

    public IReadOnlyList<VoiceInfo> GetVoices(string? language) =>
        language switch
        {
            "th-TH" => new[]
            {
                new VoiceInfo("th-TH-PremwadeeNeural", "Premwadee (หญิง)", "th-TH", "Female"),
                new VoiceInfo("th-TH-NiwatNeural", "Niwat (ชาย)", "th-TH", "Male"),
            },
            "en-US" => new[]
            {
                new VoiceInfo("en-US-JennyNeural", "Jenny", "en-US", "Female"),
                new VoiceInfo("en-US-GuyNeural", "Guy", "en-US", "Male"),
            },
            _ => Array.Empty<VoiceInfo>()
        };

    public event EventHandler<SpeechRecognizedEventArgs>? TextRecognized;
    public event EventHandler<bool>? ListeningChanged;
}
```

#### 2D. Create `Infrastructure/Speech/CompositeSpeechService.cs`

Auto-selects the best backend:

```csharp
public class CompositeSpeechService : ISpeechService
{
    private readonly ISpeechService _windowsService;
    private readonly ISpeechService? _azureService;
    private ISpeechService _active;

    public CompositeSpeechService(ISettingsService settings, ICredentialStore credentials)
    {
        _windowsService = new WindowsSpeechService();

        // Try to create Azure service if key is configured
        var azureKey = credentials.GetAsync("azure_speech_key").Result;
        var azureRegion = settings.Get("azure_speech_region", "southeastasia");

        if (!string.IsNullOrEmpty(azureKey))
        {
            _azureService = new AzureSpeechService(azureKey, azureRegion);
            _active = _azureService; // prefer Azure (has Thai)
        }
        else
        {
            _active = _windowsService; // fallback to Windows
        }
    }

    public void SwitchBackend(string backendId)
    {
        _active = backendId switch
        {
            "azure" when _azureService is not null => _azureService,
            _ => _windowsService
        };
    }

    // Delegate everything to _active:
    public IReadOnlyList<SpeechBackend> AvailableBackends =>
        new[] { _windowsService, _azureService }
            .Where(s => s is not null)
            .SelectMany(s => s!.AvailableBackends)
            .ToList();

    public SpeechBackend ActiveBackend => _active.AvailableBackends[0];
    public IReadOnlyList<string> SupportedLanguages => _active.SupportedLanguages;
    public Task StartRecognitionAsync(string language, CancellationToken ct) => _active.StartRecognitionAsync(language, ct);
    public Task StopRecognitionAsync() => _active.StopRecognitionAsync();
    public Task SpeakAsync(string text, string? voice, double speed, CancellationToken ct) => _active.SpeakAsync(text, voice, speed, ct);
    public void StopSpeaking() => _active.StopSpeaking();
    public IReadOnlyList<VoiceInfo> GetVoices(string? language) => _active.GetVoices(language);

    public event EventHandler<SpeechRecognizedEventArgs>? TextRecognized
    {
        add => _active.TextRecognized += value;
        remove => _active.TextRecognized -= value;
    }
    public event EventHandler<bool>? ListeningChanged
    {
        add => _active.ListeningChanged += value;
        remove => _active.ListeningChanged -= value;
    }
}
```

#### 2E. Wire into DI

```csharp
// In App.xaml.cs or ServiceRegistration:
services.AddSingleton<ISpeechService, CompositeSpeechService>();
```

#### 2F. Update VoiceSettingsView

Add a "Speech Backend" dropdown:
- Windows Built-in (default, ไม่ต้อง config)
- Azure AI Speech (ต้องใส่ key)

When Azure is selected but no key: show an inline text field for the key + region, with a "Test" button. Save key to DPAPI via `ICredentialStore`.

Add language dropdown that shows `SupportedLanguages` from active backend. When Azure is active and Thai is selected → mic works for Thai.

#### 2G. NuGet package

```xml
<PackageReference Include="Microsoft.CognitiveServices.Speech" Version="1.*" />
```

#### Deploy time
Just add Azure Speech key in Voice Settings → Thai speech works immediately. No code change needed.

---

## Fix 3 — Branch State Persistence

### Problem
Branch data (which messages belong to which branch, parent relationships) lives only in `MessageViewModel` properties at runtime. Restarting the app or reloading a conversation flattens everything to a single thread.

### What to build

Persist branch relationships in SQLite alongside existing message storage.

#### 3A. Add branch table to SQLite schema

In the SQLite migration (wherever `IConversationStore` creates its tables):

```sql
-- Add to existing messages table or create a branch metadata table
CREATE TABLE IF NOT EXISTS message_branches (
    message_id     TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    parent_id      TEXT,              -- NULL for root messages
    branch_index   INTEGER NOT NULL DEFAULT 0,
    branch_count   INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_branch_parent ON message_branches(parent_id);
CREATE INDEX IF NOT EXISTS idx_branch_conversation ON message_branches(conversation_id);
```

#### 3B. Update `IConversationStore`

Add branch-aware methods:

```csharp
// Save branch info when a message is created
Task SaveBranchInfoAsync(string messageId, string conversationId,
    string? parentId, int branchIndex, CancellationToken ct = default);

// Load branch tree for a conversation
Task<List<BranchInfo>> GetBranchTreeAsync(string conversationId, CancellationToken ct = default);

// Update branch count when a new branch is created from an edit
Task IncrementBranchCountAsync(string parentId, CancellationToken ct = default);
```

```csharp
public record BranchInfo(
    string MessageId,
    string ConversationId,
    string? ParentId,
    int BranchIndex,
    int BranchCount);
```

#### 3C. Save branches on message creation

In `ChatViewModel`, when the user edits a message and creates a new branch:

```csharp
// Existing edit flow creates new messages — now also persist branch info
private async Task CreateBranchAsync(MessageViewModel editedMessage, string newContent)
{
    // ... existing branch creation code ...

    // Persist to SQLite
    var parentId = editedMessage.Id; // The message being edited is the branch point
    await _conversationStore.IncrementBranchCountAsync(parentId);
    await _conversationStore.SaveBranchInfoAsync(
        newBranchMessage.Id,
        _activeConversation.Id,
        parentId,
        newBranchIndex,
        CancellationToken.None);
}
```

Also save branch info for ALL messages (not just branched ones), so the tree can be fully reconstructed:

```csharp
// In SendAsync or wherever messages are appended:
await _conversationStore.SaveBranchInfoAsync(
    message.Id, conversationId,
    previousMessageId, // parent = the message before this one
    branchIndex: 0,
    ct);
```

#### 3D. Restore branches on conversation load

In `ConversationSyncService.LoadConversationAsync()` or `ChatViewModel.LoadMessagesAsync()`:

```csharp
private async Task RestoreBranchesAsync(string conversationId)
{
    var branchTree = await _conversationStore.GetBranchTreeAsync(conversationId);
    if (branchTree.Count == 0) return; // No branch data (old conversations)

    // Build a lookup
    var lookup = branchTree.ToDictionary(b => b.MessageId);

    foreach (var message in Messages)
    {
        if (lookup.TryGetValue(message.Id, out var info))
        {
            message.BranchIndex = info.BranchIndex;
            message.BranchCount = info.BranchCount;
            message.ParentMessageId = info.ParentId;
        }
    }

    // Rebuild visible message chain: for each branch point,
    // only show the messages from the active (last-viewed) branch
    RebuildVisibleMessages();
}

private void RebuildVisibleMessages()
{
    // Walk from root, following the active branch at each fork
    var visible = new List<MessageViewModel>();
    var current = Messages.FirstOrDefault(m => m.ParentMessageId is null);

    while (current is not null)
    {
        visible.Add(current);
        // Find the next message in the active branch
        var children = Messages
            .Where(m => m.ParentMessageId == current.Id)
            .OrderBy(m => m.BranchIndex)
            .ToList();

        if (children.Count == 0) break;

        // Show the last-selected branch (or branch 0 by default)
        current = children.ElementAtOrDefault(current.ActiveChildBranch) ?? children[0];
    }

    // Replace displayed messages
    VisibleMessages.Clear();
    foreach (var m in visible)
        VisibleMessages.Add(m);
}
```

#### 3E. Add `ActiveChildBranch` to `MessageViewModel`

```csharp
[ObservableProperty] private int _activeChildBranch; // which branch is currently shown
```

Persist this too (add column `active_child_branch INTEGER DEFAULT 0` to `message_branches`), so reopening a conversation shows the same branch the user last viewed.

#### 3F. Backward compatibility

Old conversations won't have branch data. When `GetBranchTreeAsync` returns empty, show messages as a flat list (current behavior). No migration needed — the table is additive.

---

## New NuGet Packages

```xml
<!-- Azure Speech (Thai support) -->
<PackageReference Include="Microsoft.CognitiveServices.Speech" Version="1.*" />
```

No new packages needed for MCP client (uses built-in `HttpClient` + `System.Text.Json`) or branch persistence (uses existing `Microsoft.Data.Sqlite`).

---

## Config Summary: What to Set at Deploy Time

| Component | Config needed | Where to set | Default (works without config) |
|-----------|--------------|-------------|-------------------------------|
| MCP Connectors | Server URLs in catalog | `appsettings.connectors.json` or remote catalog URL | Embedded catalog with placeholder URLs (UI shows "not available") |
| MCP Connector auth | OAuth tokens per user | User clicks "Connect" → paste token or OAuth flow | None (connector shows "not connected") |
| Thai Speech STT | Azure Speech key + region | Voice Settings dialog → user enters key | Falls back to Windows (English only) |
| Thai Speech TTS | Same Azure key | Same | Falls back to Windows voices |
| Branch persistence | Nothing | Automatic (SQLite) | Works immediately |

---

## Verification Checklist

### MCP Remote Client
- [ ] `McpRemoteClient` compiles and handles JSON-RPC 2.0 request/response
- [ ] `InitializeAsync` sends `initialize` + `notifications/initialized`
- [ ] `ListToolsAsync` parses tool definitions from server
- [ ] `CallToolAsync` sends tool name + args, returns content
- [ ] Session ID is tracked and sent in headers
- [ ] `DisposeAsync` sends DELETE to close session
- [ ] `McpConnectorService.ExecuteRemoteToolAsync` routes through `McpRemoteClient`
- [ ] Remote tools appear in `ChatOrchestrator` tool list when connector is installed
- [ ] Connection status dots show in Connectors UI (green/red/grey)
- [ ] Graceful error handling: server unreachable → friendly error message, not crash

### Speech Abstraction
- [ ] `ISpeechService` interface is clean and complete
- [ ] `WindowsSpeechService` wraps existing code, works as before
- [ ] `AzureSpeechService` compiles (even without key — just won't connect)
- [ ] `CompositeSpeechService` auto-selects Azure when key is present
- [ ] Voice Settings shows backend selector dropdown
- [ ] Azure key entry field appears when Azure is selected
- [ ] "Test" button validates the key
- [ ] Language dropdown shows Thai when Azure is active
- [ ] Key is stored in DPAPI, not plaintext
- [ ] No key configured → falls back to Windows, no error

### Branch Persistence
- [ ] `message_branches` table created on first run
- [ ] Branch info saved when user edits a message
- [ ] Branch info saved for all new messages (parent chain)
- [ ] Closing and reopening a conversation restores branch state
- [ ] Branch navigation (◀ 1/2 ▶) works after reload
- [ ] Active branch selection persists (reopen shows same branch user last viewed)
- [ ] Old conversations without branch data load normally (flat list)
- [ ] Deleting a conversation cascades to delete branch data

### General
- [ ] Build: 0 errors, 0 warnings
- [ ] All existing 148 tests pass
- [ ] New tests for McpRemoteClient (mock HTTP), branch persistence (SQLite), speech service selection
- [ ] Dark/light theme: no visual regressions
