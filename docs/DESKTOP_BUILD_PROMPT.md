# SRAN Desktop — Build Prompt

> Copy this entire file as a prompt to Claude Fable (or any coding agent) with the ai-gateway codebase attached.
> It contains everything needed to scaffold and build the WPF Desktop Chat Client.

---

## YOUR MISSION

Build a native Windows desktop chat application called **SRAN Desktop** using C#/.NET 8/WPF. This app connects to an existing AI Gateway backend. The backend is already running — you're building the CLIENT only.

This is NOT just a chatbot UI. It's an **AI Agent** that can execute tools on the user's desktop (file access, browser, clipboard, shell commands) via the Model Context Protocol (MCP). Think Claude Desktop or ChatGPT Codex — but self-hosted with DLP/PII protection built-in.

## WHAT EXISTS ALREADY

The `ai-gateway` codebase has these running services:

| Service | Tech | Port | Purpose |
|---|---|---|---|
| `gateway` | Go | :8080 | Reverse proxy, API key auth, metrics |
| `backend` | Python/FastAPI | behind gateway | Core API: auth, DLP, policy, provider routing |
| `chat-edge-api` | Go | :4000 | Chat service: conversations, files, RAG, voice, memory |
| `admin-ui` | Next.js | :3000 | Admin dashboard |
| `portal` | Next.js | :3001 | Self-service portal |
| `web-chat` | Next.js | :3002 | Existing web chat client (reference implementation) |

You will build a new WPF app that connects to `chat-edge-api` (:4000) for chat, and `gateway` (:8080) for agent/tool mode.

---

## ARCHITECTURE OVERVIEW

### Dual-API Strategy

The app has TWO modes:

**Chat Mode** (default) — uses `chat-edge-api` at `:4000/api/v1/`
- Server manages conversation history, files, RAG, voice
- SSE streaming with named events (`event: content_delta\ndata: {...}`)
- Same contract as the existing web-chat

**Agent Mode** (when user enables MCP tools) — uses `gateway` at `:8080/v1/chat/completions`
- OpenAI-compatible stateless API
- Client manages conversation history locally (SQLite)
- Client sends `tools[]` in request, receives `tool_calls` in response
- Client executes tools locally, sends results back, loops until done

### Layer Architecture (Clean Architecture + MVVM)

```
Presentation (WPF/XAML)  →  Views + Controls
     ↓
ViewModel (CommunityToolkit.Mvvm)  →  State + Commands
     ↓
Application Services  →  ChatService, AgentOrchestrator, AuthService
     ↓
Infrastructure  →  HttpClients, SQLite, MCP Tools, DPAPI Vault
```

---

## PROJECT STRUCTURE — CREATE EXACTLY THIS

```
SranDesktop/
├── SranDesktop.sln
│
├── src/
│   ├── SranDesktop.App/                         # WPF startup project
│   │   ├── SranDesktop.App.csproj               # <OutputType>WinExe</OutputType>, net8.0-windows
│   │   ├── App.xaml
│   │   ├── App.xaml.cs                          # DI container setup, startup
│   │   ├── appsettings.json                     # server URLs, defaults
│   │   └── Hosting/
│   │       └── AppHostBuilder.cs                # IHostBuilder with DI registration
│   │
│   ├── SranDesktop.Presentation/                # Views + ViewModels
│   │   ├── SranDesktop.Presentation.csproj      # net8.0-windows, <UseWPF>true</UseWPF>
│   │   ├── Views/
│   │   │   ├── MainWindow.xaml                  # Shell: sidebar + content area
│   │   │   ├── MainWindow.xaml.cs
│   │   │   ├── ChatView.xaml                    # Message list + input box
│   │   │   ├── ChatView.xaml.cs
│   │   │   ├── SidebarView.xaml                 # Conversation list + folders
│   │   │   ├── SidebarView.xaml.cs
│   │   │   ├── LoginView.xaml
│   │   │   ├── LoginView.xaml.cs
│   │   │   ├── SettingsView.xaml
│   │   │   ├── SettingsView.xaml.cs
│   │   │   ├── AgentPanelView.xaml              # Tool execution log
│   │   │   ├── AgentPanelView.xaml.cs
│   │   │   └── Dialogs/
│   │   │       ├── ToolConsentDialog.xaml
│   │   │       └── ToolConsentDialog.xaml.cs
│   │   ├── ViewModels/
│   │   │   ├── MainViewModel.cs
│   │   │   ├── ChatViewModel.cs
│   │   │   ├── ConversationListViewModel.cs
│   │   │   ├── AgentExecutionViewModel.cs
│   │   │   ├── LoginViewModel.cs
│   │   │   └── SettingsViewModel.cs
│   │   ├── Controls/
│   │   │   ├── MarkdownRenderer.cs              # Markdig → FlowDocument
│   │   │   ├── StreamingTextBlock.cs            # Incremental FlowDocument update
│   │   │   ├── MessageBubble.xaml               # Single message with avatar + actions
│   │   │   ├── MessageBubble.xaml.cs
│   │   │   ├── FileDropZone.xaml
│   │   │   └── FileDropZone.xaml.cs
│   │   ├── Converters/
│   │   │   ├── BoolToVisibilityConverter.cs
│   │   │   ├── RoleToAlignmentConverter.cs
│   │   │   └── TimestampConverter.cs
│   │   └── Resources/
│   │       ├── Themes/
│   │       │   ├── Light.xaml
│   │       │   └── Dark.xaml
│   │       ├── Styles.xaml                      # Global styles
│   │       └── Icons.xaml                       # Path geometries
│   │
│   ├── SranDesktop.Application/                 # Business logic (no UI refs)
│   │   ├── SranDesktop.Application.csproj       # net8.0 (NOT windows-specific)
│   │   ├── Services/
│   │   │   ├── ChatService.cs                   # Send message, parse SSE, emit events
│   │   │   ├── AgentOrchestrator.cs             # Tool loop: detect→consent→execute→loop
│   │   │   ├── ConversationSyncService.cs       # Server ↔ SQLite sync
│   │   │   ├── FileService.cs                   # Upload files to chat-edge-api
│   │   │   ├── AuthService.cs                   # Login, token refresh, logout
│   │   │   └── NotificationService.cs           # Toast notifications
│   │   ├── Interfaces/
│   │   │   ├── IChatService.cs
│   │   │   ├── IAgentOrchestrator.cs
│   │   │   ├── IConversationStore.cs
│   │   │   ├── ICredentialStore.cs
│   │   │   └── IMcpToolHost.cs
│   │   ├── Models/
│   │   │   ├── ChatCompletionRequest.cs
│   │   │   ├── ChatCompletionResponse.cs
│   │   │   ├── ChatCompletionChunk.cs           # SSE streaming chunk
│   │   │   ├── SseFrame.cs
│   │   │   ├── ToolCallInfo.cs
│   │   │   └── ToolResult.cs
│   │   └── Events/
│   │       ├── ContentDeltaEvent.cs
│   │       ├── StreamStartedEvent.cs
│   │       ├── StreamCompletedEvent.cs
│   │       └── ToolCallRequestedEvent.cs
│   │
│   ├── SranDesktop.Domain/                      # Pure domain models
│   │   ├── SranDesktop.Domain.csproj            # net8.0, ZERO dependencies
│   │   ├── Entities/
│   │   │   ├── Conversation.cs
│   │   │   ├── Message.cs
│   │   │   ├── FileAttachment.cs
│   │   │   └── ToolExecution.cs
│   │   ├── Enums/
│   │   │   ├── MessageRole.cs                   # System, User, Assistant, Tool
│   │   │   ├── ConversationSource.cs            # Server, Local
│   │   │   ├── PermissionLevel.cs               # Ask, AllowOnce, AllowAlways, Deny
│   │   │   └── ToolCategory.cs                  # FileSystem, Browser, Clipboard, Shell, Screen
│   │   └── ValueObjects/
│   │       └── TokenUsage.cs
│   │
│   ├── SranDesktop.Infrastructure/              # External integrations
│   │   ├── SranDesktop.Infrastructure.csproj    # net8.0-windows (needs DPAPI)
│   │   ├── Api/
│   │   │   ├── GatewayApiClient.cs              # /v1/chat/completions with tools
│   │   │   ├── ChatEdgeApiClient.cs             # /api/v1/ conversations, files
│   │   │   └── SseStreamReader.cs               # Stream → IAsyncEnumerable<SseFrame>
│   │   ├── Storage/
│   │   │   ├── SqliteConversationStore.cs        # IConversationStore impl
│   │   │   └── DpapiCredentialStore.cs           # ICredentialStore impl (Windows DPAPI)
│   │   └── Mcp/
│   │       ├── McpToolHost.cs                    # IMcpToolHost impl
│   │       ├── McpToolRegistry.cs
│   │       └── Tools/
│   │           ├── ReadFileTool.cs
│   │           ├── WriteFileTool.cs
│   │           ├── ListDirectoryTool.cs
│   │           ├── SearchFilesTool.cs
│   │           ├── OpenUrlTool.cs
│   │           ├── ReadClipboardTool.cs
│   │           ├── WriteClipboardTool.cs
│   │           ├── RunCommandTool.cs
│   │           └── ScreenshotTool.cs
│   │
│   └── SranDesktop.Tests/
│       ├── SranDesktop.Tests.csproj
│       ├── Services/
│       │   ├── ChatServiceTests.cs
│       │   ├── AgentOrchestratorTests.cs
│       │   └── SseStreamReaderTests.cs
│       └── Mcp/
│           └── McpToolHostTests.cs
│
└── docs/
    └── ARCHITECTURE.md
```

---

## NUGET PACKAGES

### SranDesktop.App
```xml
<PackageReference Include="Microsoft.Extensions.Hosting" Version="8.0.*" />
<PackageReference Include="Serilog.Extensions.Hosting" Version="8.0.*" />
<PackageReference Include="Serilog.Sinks.File" Version="5.*" />
<PackageReference Include="H.NotifyIcon.Wpf" Version="2.*" />
```

### SranDesktop.Presentation
```xml
<PackageReference Include="CommunityToolkit.Mvvm" Version="8.*" />
<PackageReference Include="Markdig" Version="0.37.*" />
<PackageReference Include="Microsoft.Extensions.DependencyInjection" Version="8.0.*" />
```

### SranDesktop.Application
```xml
<PackageReference Include="Microsoft.Extensions.Logging.Abstractions" Version="8.0.*" />
<PackageReference Include="System.Text.Json" Version="8.0.*" />
```

### SranDesktop.Infrastructure
```xml
<PackageReference Include="Microsoft.Data.Sqlite" Version="8.0.*" />
<PackageReference Include="System.Security.Cryptography.ProtectedData" Version="8.0.*" />
<PackageReference Include="Microsoft.Extensions.Http" Version="8.0.*" />
```

### SranDesktop.Tests
```xml
<PackageReference Include="xunit" Version="2.*" />
<PackageReference Include="xunit.runner.visualstudio" Version="2.*" />
<PackageReference Include="Moq" Version="4.*" />
<PackageReference Include="FluentAssertions" Version="6.*" />
```

---

## API CONTRACTS — EXACT SPECIFICATIONS

### A. Chat-Edge-API (Chat Mode) — Base URL: `http://localhost:4000/api/v1`

#### Auth: Portal JWT via cookie or `Authorization: Bearer {jwt}`

**Login:**
```
POST /api/v1/auth/login
Body: { "email": "string", "password": "string" }
Response: { "access_token": "jwt...", "refresh_token": "jwt...", "user": { "id", "email", "display_name" } }
```

**Refresh:**
```
POST /api/v1/auth/refresh
Body: { "refresh_token": "string" }
Response: { "access_token": "jwt...", "refresh_token": "jwt..." }
```

**List Conversations:**
```
GET /api/v1/conversations?cursor={id}&limit=20
Headers: Authorization: Bearer {access_token}
Response: { "conversations": [...], "next_cursor": "id|null" }
```

**Create Conversation:**
```
POST /api/v1/conversations
Body: { "model_id": "string" }
Response: { "id", "title", "model_id", "created_at" }
```

**Send Message (SSE streaming):**
```
POST /api/v1/conversations/{id}/messages
Headers: Authorization: Bearer {access_token}
Body: { "content": "string", "file_ids": ["id1"] }
Response: SSE stream (Content-Type: text/event-stream)
```

**SSE Event Types from chat-edge-api:**
```
event: message_start
data: {"message_id":"msg_xxx","model_id":"gpt-4o"}

event: content_delta
data: {"text":"Hello"}

event: content_delta
data: {"text":" world"}

event: reasoning_delta
data: {"text":"Let me think..."}

event: analysis_status
data: {"status":"started"}

event: search_status
data: {"status":"searching","query":"..."}

event: message_sources
data: [{"title":"...","url":"...","snippet":"..."}]

event: image_generating
data: {"status":"generating","prompt":"..."}

event: message_files
data: [{"id":"f1","name":"output.xlsx","url":"/files/f1","type":"application/xlsx"}]

event: fallback_info
data: {"requested_model":"gpt-4o","actual_model":"gpt-4o-mini","reason":"rate_limited"}

event: message_complete
data: {"token_usage":{"prompt_tokens":150,"completion_tokens":80,"total_tokens":230},"response_time_ms":1234}

event: error
data: {"code":"provider_error","message":"Rate limited"}
```

**Upload File:**
```
POST /api/v1/conversations/{id}/files
Headers: Authorization: Bearer {access_token}
Content-Type: multipart/form-data
Body: file=@file.pdf
Response: { "id": "file_xxx", "name": "file.pdf", "type": "application/pdf", "size": 12345, "url": "/files/file_xxx" }
```

**List Models:**
```
GET /api/v1/models
Response: { "models": [{ "id": "gpt-4o", "name": "GPT-4o", "provider": "openai", "capabilities": [...] }] }
```

**Delete Conversation:**
```
DELETE /api/v1/conversations/{id}
Response: 204 No Content
```

**Rename Conversation:**
```
PATCH /api/v1/conversations/{id}
Body: { "title": "New Title" }
Response: { "id", "title" }
```

### B. Gateway API (Agent Mode) — Base URL: `http://localhost:8080`

#### Auth: API key via `Authorization: Bearer gw_xxxx`

**Chat Completions (OpenAI-compatible):**
```
POST /v1/chat/completions
Headers: Authorization: Bearer gw_xxxx
Body:
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant with access to tools."},
    {"role": "user", "content": "Read the file report.xlsx and summarize it"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "read_file",
        "description": "Read a file from the local filesystem",
        "parameters": {
          "type": "object",
          "properties": {
            "path": {"type": "string", "description": "Absolute file path"}
          },
          "required": ["path"]
        }
      }
    }
  ],
  "stream": true
}
```

**SSE Streaming Response (OpenAI format):**
```
data: {"id":"req_xxx","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"req_xxx","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"req_xxx","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}

data: {"id":"req_xxx","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

**Tool Call in SSE Stream:**
```
data: {"id":"req_xxx","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_xxx","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}

data: {"id":"req_xxx","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"path\":\"/users/beat/report.xlsx\"}"}}]},"finish_reason":null}]}

data: {"id":"req_xxx","object":"chat.completion.chunk","created":1720000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
```

**Sending Tool Results Back:**
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "Read report.xlsx and summarize"},
    {"role": "assistant", "content": null, "tool_calls": [
      {"id": "call_xxx", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"/users/beat/report.xlsx\"}"}}
    ]},
    {"role": "tool", "tool_call_id": "call_xxx", "name": "read_file", "content": "Q1 Revenue: $1.2M\nQ2 Revenue: $1.5M\n..."}
  ],
  "tools": [...same tools...],
  "stream": true
}
```

**Non-streaming Response:**
```json
{
  "id": "req_xxx",
  "object": "chat.completion",
  "created": 1720000000,
  "model": "gpt-4o",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Here's the summary...",
      "tool_calls": null
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 80,
    "total_tokens": 230,
    "estimated_cost_usd": 0.0023
  }
}
```

**Response Headers (informational):**
```
X-Gateway-Provider: openai
X-Gateway-Model: gpt-4o
X-Gateway-Fallback: false
X-Gateway-Attempt: 1
X-Request-Id: req_xxx
```

---

## SQLITE SCHEMA

Create this on first launch at `%APPDATA%\SRAN Desktop\data.db`:

```sql
CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    title           TEXT,
    model_id        TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_pinned       INTEGER DEFAULT 0,
    folder_id       TEXT,
    source          TEXT NOT NULL DEFAULT 'server' CHECK(source IN ('server','local')),
    last_synced_at  TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
    content         TEXT,
    tool_calls      TEXT,
    tool_call_id    TEXT,
    name            TEXT,
    model_id        TEXT,
    token_usage     TEXT,
    created_at      TEXT NOT NULL,
    sort_order      INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS files (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    name            TEXT NOT NULL,
    mime_type       TEXT,
    size_bytes      INTEGER,
    local_path      TEXT,
    server_url      TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_executions (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_id      TEXT,
    tool_name       TEXT NOT NULL,
    arguments       TEXT NOT NULL,
    result          TEXT,
    permission      TEXT NOT NULL CHECK(permission IN ('auto','allowed','denied')),
    duration_ms     INTEGER,
    error           TEXT,
    executed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_permissions (
    tool_name       TEXT NOT NULL,
    scope           TEXT NOT NULL,
    permission      TEXT NOT NULL CHECK(permission IN ('allow_always','deny_always','ask')),
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (tool_name, scope)
);

CREATE INDEX IF NOT EXISTS idx_messages_convo ON messages(conversation_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_tool_exec_convo ON tool_executions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_files_convo ON files(conversation_id);
```

---

## KEY IMPLEMENTATION DETAILS

### 1. SSE Stream Reader (Critical — get this right)

```csharp
public record SseFrame(string? Event, string Data);

public static class SseStreamReader
{
    public static async IAsyncEnumerable<SseFrame> ParseAsync(
        Stream stream,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        using var reader = new StreamReader(stream, Encoding.UTF8);
        var dataLines = new List<string>();
        string? eventType = null;

        while (!ct.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(ct);
            if (line == null) yield break;

            if (line.Length == 0)
            {
                if (dataLines.Count > 0)
                {
                    var data = string.Join("\n", dataLines);
                    yield return new SseFrame(eventType, data);
                    dataLines.Clear();
                    eventType = null;
                }
                continue;
            }

            if (line.StartsWith("event:"))
                eventType = line[6..].Trim();
            else if (line.StartsWith("data:"))
                dataLines.Add(line[5..].TrimStart());
            // ignore id:, retry:, comments (lines starting with :)
        }
    }
}
```

### 2. ChatService — Chat Mode (chat-edge-api)

```csharp
public class ChatService : IChatService
{
    // Key behaviors:
    // 1. Parse named SSE events (event: content_delta, etc.)
    // 2. Accumulate content_delta text into a StringBuilder
    // 3. Emit ContentDeltaEvent on each delta (for UI streaming)
    // 4. Handle reasoning_delta separately (collapsible in UI)
    // 5. On message_complete, save full message to SQLite
    // 6. On error event, emit error and stop
    // 7. Support cancellation via CancellationToken

    public async IAsyncEnumerable<ChatStreamEvent> SendMessageAsync(
        string conversationId, string content, List<string>? fileIds,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var response = await _chatEdgeClient.PostMessageAsync(conversationId, content, fileIds, ct);
        var stream = await response.Content.ReadAsStreamAsync(ct);

        await foreach (var frame in SseStreamReader.ParseAsync(stream, ct))
        {
            switch (frame.Event)
            {
                case "content_delta":
                    var delta = JsonSerializer.Deserialize<ContentDelta>(frame.Data);
                    yield return new ContentDeltaEvent(delta.Text);
                    break;
                case "message_start":
                    var start = JsonSerializer.Deserialize<MessageStart>(frame.Data);
                    yield return new StreamStartedEvent(start.MessageId, start.ModelId);
                    break;
                case "message_complete":
                    var complete = JsonSerializer.Deserialize<MessageComplete>(frame.Data);
                    yield return new StreamCompletedEvent(complete.TokenUsage);
                    break;
                case "error":
                    var error = JsonSerializer.Deserialize<StreamError>(frame.Data);
                    yield return new StreamErrorEvent(error.Code, error.Message);
                    break;
                // handle other events...
            }
        }
    }
}
```

### 3. AgentOrchestrator — Agent Mode (gateway)

```csharp
public class AgentOrchestrator : IAgentOrchestrator
{
    private const int MaxIterations = 20;
    private static readonly TimeSpan MaxDuration = TimeSpan.FromMinutes(5);

    public async IAsyncEnumerable<AgentEvent> ExecuteAsync(
        List<Message> history, string userMessage,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var messages = new List<ChatMessage>(history.Select(m => m.ToChatMessage()));
        messages.Add(new ChatMessage("user", userMessage));

        var tools = _toolHost.GetRegisteredTools()
            .Select(t => t.ToOpenAiToolDefinition())
            .ToList();

        var stopwatch = Stopwatch.StartNew();

        for (int iteration = 0; iteration < MaxIterations; iteration++)
        {
            if (stopwatch.Elapsed > MaxDuration)
            {
                yield return new AgentEvent.Timeout();
                yield break;
            }

            // Send to gateway
            var request = new ChatCompletionRequest
            {
                Model = _settings.SelectedModel,
                Messages = messages,
                Tools = tools,
                Stream = true
            };

            var content = new StringBuilder();
            var toolCalls = new List<ToolCallInfo>();

            await foreach (var chunk in _gatewayClient.StreamAsync(request, ct))
            {
                if (chunk.Delta?.Content is { } text)
                {
                    content.Append(text);
                    yield return new AgentEvent.ContentDelta(text);
                }

                if (chunk.Delta?.ToolCalls is { } calls)
                {
                    // Accumulate tool call arguments (they stream in chunks)
                    foreach (var call in calls)
                        AccumulateToolCall(toolCalls, call);
                }

                if (chunk.FinishReason == "stop")
                {
                    yield return new AgentEvent.Completed(content.ToString());
                    yield break;
                }

                if (chunk.FinishReason == "tool_calls")
                    break; // proceed to tool execution
            }

            // Execute tool calls
            if (toolCalls.Count > 0)
            {
                // Add assistant message with tool_calls
                messages.Add(new ChatMessage
                {
                    Role = "assistant",
                    Content = content.Length > 0 ? content.ToString() : null,
                    ToolCalls = toolCalls.Select(tc => tc.ToApiFormat()).ToList()
                });

                foreach (var toolCall in toolCalls)
                {
                    yield return new AgentEvent.ToolCallRequested(toolCall);

                    // Check permission
                    var permission = await _toolHost.CheckPermissionAsync(toolCall);
                    if (permission == PermissionResult.NeedsConsent)
                    {
                        yield return new AgentEvent.ConsentRequired(toolCall);
                        // Wait for UI to approve/deny via TaskCompletionSource
                        var approved = await _consentAwaiter.WaitAsync(toolCall.Id, ct);
                        if (!approved)
                        {
                            messages.Add(new ChatMessage
                            {
                                Role = "tool",
                                ToolCallId = toolCall.Id,
                                Name = toolCall.Name,
                                Content = "Error: User denied permission for this tool."
                            });
                            yield return new AgentEvent.ToolDenied(toolCall);
                            continue;
                        }
                    }

                    // Execute
                    var result = await _toolHost.ExecuteAsync(toolCall, ct);
                    yield return new AgentEvent.ToolExecuted(toolCall, result);

                    messages.Add(new ChatMessage
                    {
                        Role = "tool",
                        ToolCallId = toolCall.Id,
                        Name = toolCall.Name,
                        Content = result.Content.Length > 50_000
                            ? result.Content[..50_000] + "\n...[truncated]"
                            : result.Content
                    });
                }
            }
        }

        yield return new AgentEvent.MaxIterationsReached();
    }
}
```

### 4. DPAPI Credential Storage

```csharp
public class DpapiCredentialStore : ICredentialStore
{
    private readonly string _filePath;

    public DpapiCredentialStore()
    {
        var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        var dir = Path.Combine(appData, "SRAN Desktop");
        Directory.CreateDirectory(dir);
        _filePath = Path.Combine(dir, "credentials.dat");
    }

    public void Save(string key, string value)
    {
        var data = LoadAll();
        data[key] = value;
        var json = JsonSerializer.Serialize(data);
        var encrypted = ProtectedData.Protect(
            Encoding.UTF8.GetBytes(json),
            null,
            DataProtectionScope.CurrentUser);
        File.WriteAllBytes(_filePath, encrypted);
    }

    public string? Load(string key)
    {
        var data = LoadAll();
        return data.TryGetValue(key, out var value) ? value : null;
    }

    private Dictionary<string, string> LoadAll()
    {
        if (!File.Exists(_filePath)) return new();
        var encrypted = File.ReadAllBytes(_filePath);
        var decrypted = ProtectedData.Unprotect(encrypted, null, DataProtectionScope.CurrentUser);
        return JsonSerializer.Deserialize<Dictionary<string, string>>(decrypted) ?? new();
    }
}
```

### 5. Markdown → FlowDocument Renderer

```csharp
// Use Markdig to parse markdown, then convert to WPF FlowDocument
// Key points:
// - Use MarkdownPipeline with GFM extensions
// - Convert headings → Bold Run with larger font
// - Convert code blocks → RichTextBox with monospace font + background
// - Convert tables → WPF Table element
// - Convert inline code → Run with monospace + background
// - Convert links → Hyperlink that opens in default browser
// - Convert images → InlineUIContainer with Image control
// - For streaming: don't rebuild entire FlowDocument on each delta
//   Instead, append to the last Paragraph, rebuild only when
//   markdown structure changes (new heading, code block, etc.)

public class MarkdownToFlowDocumentConverter
{
    private readonly MarkdownPipeline _pipeline;

    public MarkdownToFlowDocumentConverter()
    {
        _pipeline = new MarkdownPipelineBuilder()
            .UseAdvancedExtensions()  // GFM tables, task lists, etc.
            .Build();
    }

    public FlowDocument Convert(string markdown)
    {
        var doc = new FlowDocument();
        var parsed = Markdown.Parse(markdown, _pipeline);

        foreach (var block in parsed)
        {
            switch (block)
            {
                case HeadingBlock heading:
                    doc.Blocks.Add(ConvertHeading(heading));
                    break;
                case ParagraphBlock para:
                    doc.Blocks.Add(ConvertParagraph(para));
                    break;
                case FencedCodeBlock code:
                    doc.Blocks.Add(ConvertCodeBlock(code));
                    break;
                case ListBlock list:
                    doc.Blocks.Add(ConvertList(list));
                    break;
                case Table table:
                    doc.Blocks.Add(ConvertTable(table));
                    break;
                // ... handle other block types
            }
        }

        return doc;
    }
}
```

### 6. MCP Tool Example — ReadFileTool

```csharp
public class ReadFileTool : IMcpTool
{
    public string Name => "read_file";
    public string Description => "Read the contents of a file from the local filesystem";
    public ToolCategory Category => ToolCategory.FileSystem;
    public PermissionLevel DefaultPermission => PermissionLevel.Ask;

    public JsonElement ParametersSchema => JsonDocument.Parse("""
    {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to read"
            }
        },
        "required": ["path"]
    }
    """).RootElement;

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = arguments.GetProperty("path").GetString()
            ?? throw new ArgumentException("path is required");

        // Security: canonicalize and validate
        var fullPath = Path.GetFullPath(path);

        if (!File.Exists(fullPath))
            return ToolResult.Error($"File not found: {fullPath}");

        var info = new FileInfo(fullPath);
        if (info.Length > 10 * 1024 * 1024) // 10MB limit
            return ToolResult.Error($"File too large: {info.Length} bytes (max 10MB)");

        // Detect binary
        if (IsBinaryFile(fullPath))
            return ToolResult.Error("Binary file — cannot read as text. Use a specific tool for this file type.");

        var content = await File.ReadAllTextAsync(fullPath, ct);

        // Truncate if needed
        if (content.Length > 50_000)
            content = content[..50_000] + $"\n...[truncated, total {content.Length} chars]";

        return ToolResult.Success(content);
    }

    private static bool IsBinaryFile(string path)
    {
        var buffer = new byte[8192];
        using var fs = File.OpenRead(path);
        var bytesRead = fs.Read(buffer, 0, buffer.Length);
        return buffer[..bytesRead].Any(b => b == 0);
    }
}
```

---

## UI DESIGN GUIDELINES

### Layout
```
┌──────────────────────────────────────────────────────────┐
│  ─ □ ✕  SRAN Desktop                    [⚙] [🌙]       │
├────────────┬─────────────────────────────┬───────────────┤
│            │                             │               │
│  [+ New]   │  ┌─────────────────────┐    │  Agent Panel  │
│            │  │ 🤖 Assistant        │    │  (collapsible)│
│  Search... │  │ Here's the summary  │    │               │
│            │  │ of your report:     │    │  ┌──────────┐ │
│  Today     │  │                     │    │  │read_file │ │
│  ├ Chat 1  │  │ **Revenue** grew... │    │  │✅ allowed │ │
│  ├ Chat 2  │  │ ```python           │    │  │ 245ms    │ │
│  │         │  │ df.describe()       │    │  └──────────┘ │
│  Yesterday │  │ ```                 │    │               │
│  ├ Chat 3  │  └─────────────────────┘    │  ┌──────────┐ │
│  │         │                             │  │write_file│ │
│  Folders   │  ┌─────────────────────┐    │  │⏳ pending │ │
│  ├ Work    │  │ 👤 You              │    │  └──────────┘ │
│  ├ Personal│  │ สรุปไฟล์ report ให้  │    │               │
│            │  └─────────────────────┘    │               │
│            │                             │               │
│            ├─────────────────────────────┤               │
│            │ [📎] Type a message...  [➤] │               │
│            │ [🔧 Agent Mode: ON]         │               │
├────────────┴─────────────────────────────┴───────────────┤
│  Model: GPT-4o ▼  │  Tokens: 230  │  Cost: ฿0.08       │
└──────────────────────────────────────────────────────────┘
```

### Theme Colors
```xaml
<!-- Light Theme -->
<Color x:Key="BgPrimary">#FFFFFF</Color>
<Color x:Key="BgSecondary">#F8FAFC</Color>
<Color x:Key="BgSidebar">#F1F5F9</Color>
<Color x:Key="TextPrimary">#1E293B</Color>
<Color x:Key="TextSecondary">#64748B</Color>
<Color x:Key="AccentBlue">#3B82F6</Color>
<Color x:Key="AccentGreen">#22C55E</Color>
<Color x:Key="AccentRed">#EF4444</Color>
<Color x:Key="Border">#E2E8F0</Color>
<Color x:Key="UserBubble">#3B82F6</Color>
<Color x:Key="AssistantBubble">#F1F5F9</Color>
<Color x:Key="CodeBg">#1E293B</Color>

<!-- Dark Theme -->
<Color x:Key="BgPrimary">#0F172A</Color>
<Color x:Key="BgSecondary">#1E293B</Color>
<Color x:Key="BgSidebar">#1E293B</Color>
<Color x:Key="TextPrimary">#F8FAFC</Color>
<Color x:Key="TextSecondary">#94A3B8</Color>
<Color x:Key="AccentBlue">#60A5FA</Color>
<Color x:Key="AccentGreen">#4ADE80</Color>
<Color x:Key="AccentRed">#F87171</Color>
<Color x:Key="Border">#334155</Color>
<Color x:Key="UserBubble">#2563EB</Color>
<Color x:Key="AssistantBubble">#1E293B</Color>
<Color x:Key="CodeBg">#0F172A</Color>
```

### Key UX Behaviors
1. **Streaming**: Content appears character-by-character, smoothly — use DispatcherTimer at 16ms (60fps) to batch FlowDocument updates
2. **Auto-scroll**: Scroll to bottom during streaming, but stop auto-scroll if user scrolls up
3. **Code blocks**: Monospace font, syntax highlighting, copy button on hover
4. **File drop**: Drag files onto chat area to upload, show preview thumbnail
5. **Agent panel**: Slides in from right when Agent Mode is active, shows real-time tool execution log
6. **Consent dialog**: Modal overlay showing tool name, arguments, allow/deny buttons. Must be non-blocking (async/await with TaskCompletionSource)
7. **Message actions**: Copy, regenerate, feedback (thumbs up/down) on hover
8. **Keyboard shortcuts**: Enter = send, Shift+Enter = newline, Ctrl+Shift+Space = global hotkey (Phase 3)

---

## BUILD ORDER — PHASE 1 (do these in order)

### Step 1: Scaffold Solution
- Create .sln and all 6 .csproj files
- Set up project references (App → Presentation → Application → Domain, Infrastructure)
- Add all NuGet packages
- Create App.xaml with DI bootstrap
- Verify it compiles and shows an empty window

### Step 2: Domain + Infrastructure (Auth)
- Create all Domain entities and enums
- Implement DpapiCredentialStore
- Implement basic ChatEdgeApiClient (just login + refresh)
- Implement AuthService (login, save tokens, refresh on 401)

### Step 3: Login View
- Create LoginView with email/password fields
- Create LoginViewModel with LoginCommand
- Wire up auth flow: login → save token → navigate to main
- Handle errors (wrong password, network)

### Step 4: Main Shell + Sidebar
- Create MainWindow with sidebar + content area layout
- Create SidebarView with conversation list
- Implement ConversationSyncService (fetch from server, save to SQLite)
- Create ConversationListViewModel (load, create, delete, rename, search)

### Step 5: Chat View + SSE Streaming
- Implement SseStreamReader
- Implement ChatService with SendMessageAsync
- Create ChatView with message list (ItemsControl + DataTemplate)
- Create ChatViewModel with SendCommand
- Wire up streaming: SSE → ChatService events → ViewModel → UI
- Implement auto-scroll behavior

### Step 6: Markdown Rendering
- Implement MarkdownToFlowDocumentConverter using Markdig
- Handle: headings, paragraphs, bold/italic, code blocks, inline code, tables, links, lists
- Create MessageBubble control (avatar + rendered content + timestamp)
- Optimize for streaming (incremental FlowDocument updates)

### Step 7: File Upload
- Implement FileService with UploadAsync (XHR-style with progress)
- Create FileDropZone control
- Add attach button to chat input
- Show upload progress in UI
- Send file_ids with message

### Step 8: Settings + Theme
- Create SettingsView (server URL, theme toggle, model selection)
- Implement Light/Dark theme switching via DynamicResource
- Persist settings to appsettings.json

### Step 9: Polish + Test
- Add error handling for network failures
- Add loading states (skeleton UI while fetching)
- Write unit tests for SseStreamReader, ChatService, AgentOrchestrator
- Test with real backend
- Fix edge cases (empty conversations, long messages, reconnect)

---

## PHASE 2 (after Phase 1 works): Agent + MCP

### Step 10: Gateway Client + Agent Mode Toggle
- Implement GatewayApiClient with tool_use support
- Add Agent Mode toggle button to chat toolbar
- When toggled ON: show agent panel, use gateway API, include tools[]

### Step 11: MCP Tool Host
- Implement McpToolRegistry and McpToolHost
- Implement all 9 tools (read_file, write_file, list_directory, search_files, open_url, read_clipboard, write_clipboard, run_command, screenshot)
- Each tool validates permissions and returns structured results

### Step 12: Agent Orchestrator
- Implement the full agent loop (detect tool_calls → consent → execute → send result → loop)
- Max 20 iterations, 5 min timeout
- ESC to cancel

### Step 13: Consent Dialog + Agent Panel
- Create ToolConsentDialog (shows tool name, args, allow/deny)
- Create AgentPanelView (list of tool executions with status)
- Permission persistence (allow_always for specific tools)

---

## IMPORTANT NOTES

1. **DO NOT use any AI/LLM library** (like Semantic Kernel, LangChain). Write raw HTTP calls. The API is simple OpenAI-compatible REST.

2. **DO NOT use Entity Framework**. Use raw Microsoft.Data.Sqlite with hand-written SQL. It's simpler and has less overhead for this use case.

3. **DO NOT create a console app**. This is a WPF desktop application with `<OutputType>WinExe</OutputType>`.

4. **Thread safety**: All UI updates MUST go through `Application.Current.Dispatcher.InvokeAsync()`. SSE parsing happens on a background thread.

5. **JSON serialization**: Use `System.Text.Json` with source-generated serialization contexts for AOT compatibility:
```csharp
[JsonSerializable(typeof(ChatCompletionRequest))]
[JsonSerializable(typeof(ChatCompletionChunk))]
internal partial class AppJsonContext : JsonSerializerContext { }
```

6. **HttpClient lifecycle**: Use `IHttpClientFactory` registered in DI. Don't `new HttpClient()` per request.

7. **Cancellation**: Every async method must accept and honor `CancellationToken`. The user can cancel streaming at any time.

8. **The existing web-chat** in `apps/web-chat/src/` is your reference implementation. Study `src/lib/stream.ts` for SSE parsing, `src/lib/api.ts` for API calls, `src/stores/chatStore.ts` for state management.

---

## REFERENCE: web-chat SSE parsing (TypeScript → replicate in C#)

```typescript
// From apps/web-chat/src/lib/stream.ts
// This is what you need to replicate in C#

export async function* parseSSE<T>(response: Response): AsyncGenerator<StreamEvent<T>> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop()!;

    for (const frame of frames) {
      if (!frame.trim()) continue;
      const event = parseFrame<T>(frame);
      if (event) yield event;
    }
  }
}

function parseFrame<T>(frame: string): StreamEvent<T> | null {
  let event = '';
  let data = '';

  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data = line.slice(5).trim();
  }

  if (!data) return null;
  return { event, data: JSON.parse(data) as T };
}
```

---

*End of Build Prompt — Good luck!*
