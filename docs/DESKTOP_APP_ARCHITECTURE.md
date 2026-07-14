# SRAN Desktop — Architecture Document

> **Version:** 1.0 · **Date:** 2026-07-07 · **Status:** Draft for Review  
> **Author:** Architecture Team · **Audience:** Engineering, Product, Leadership

---

## 1. Executive Summary

SRAN Desktop เป็น native Windows application (C#/.NET 8/WPF) ที่ทำมากกว่าแค่ chat — มันเป็น **AI Agent ที่อยู่บน desktop ของผู้ใช้** สามารถอ่านไฟล์ เปิดโปรแกรม ดูหน้าจอ และลงมือทำงานแทนได้จริง ผ่าน Model Context Protocol (MCP)

**ทำไมต้อง native app?**
- Web browser ไม่สามารถเข้าถึง file system, clipboard, หรือ OS APIs ได้
- MCP tool execution ต้อง run on user's machine — ไม่ใช่ server
- Native = first-class OS citizen: notifications, system tray, startup, file associations
- Performance: SSE parsing, markdown rendering, file I/O ไม่ต้องผ่าน JS runtime

**Scope ที่ครอบคลุม:**
- Chat ถามตอบ + streaming response (SSE)
- File upload + vision (inline image analysis)
- Chat history / multi-turn conversation management
- Markdown rendering (GFM, code highlighting, tables)
- MCP Agent — AI เรียก tools บน desktop ได้ (file access, browser, clipboard, shell)
- Offline-capable conversation cache

---

## 2. Design Principles

| Principle | Rationale |
|---|---|
| **Dual-API Strategy** | ใช้ chat-edge-api สำหรับ conversations/history/files, ใช้ gateway `/v1/chat/completions` สำหรับ agent loop ที่ต้อง tool_use |
| **Agent-First** | ไม่ใช่แค่ chat box — ทุก design decision ต้องรองรับ tool execution loop |
| **Offline-Resilient** | Local SQLite cache; อ่าน history ได้แม้ไม่มี internet |
| **Permission-Gated** | ทุก MCP tool ต้องขอ consent ก่อน execute; ไม่มี tool ไหนทำงานได้ถ้า user ไม่ allow |
| **Separation of Concerns** | Presentation ≠ Domain ≠ Infrastructure; เปลี่ยน UI framework ได้โดยไม่แตะ business logic |

---

## 3. Tech Stack

### 3.1 Core

| Layer | Choice | Rationale |
|---|---|---|
| **Runtime** | .NET 8 (LTS) | Long-term support ถึง 2026-11, PerformanceR ดีกว่า .NET Framework, Native AOT option |
| **UI Framework** | WPF | Windows-only scope, mature ecosystem, best FlowDocument support สำหรับ rich markdown, XAML data binding ดีกว่า WinUI 3 ที่ยัง buggy |
| **Architecture** | MVVM + Clean Architecture | Testable, maintainable, industry-standard สำหรับ WPF |
| **DI** | Microsoft.Extensions.DependencyInjection | Same as ASP.NET — familiar, lightweight |
| **MVVM Toolkit** | CommunityToolkit.Mvvm 8.x | Source-generated `[ObservableProperty]`, `[RelayCommand]` — zero boilerplate |

### 3.2 Key Dependencies

| Package | Purpose |
|---|---|
| `System.Net.Http` | HTTP client + custom SSE stream reader |
| `Microsoft.Data.Sqlite` | Local conversation cache |
| `Markdig` | Markdown → XAML FlowDocument (extensible pipeline) |
| `System.Text.Json` | JSON serialization (source-generated for AOT compat) |
| `Microsoft.Extensions.Logging` + Serilog | Structured logging to file + debug |
| `H.NotifyIcon` | System tray integration |
| `CommunityToolkit.Mvvm` | MVVM source generators |
| `Microsoft.Web.WebView2` | Optional: sandboxed browser tool + rich HTML preview |

### 3.3 Why NOT These

| Rejected | Reason |
|---|---|
| **.NET MAUI** | Cross-platform ไม่จำเป็น (Windows-only scope), WPF มี FlowDocument ที่ MAUI ไม่มี, MAUI rendering bugs ยัง active |
| **Electron** | ถ้าจะใช้ Electron ก็ใช้ web-chat ที่มีอยู่แล้ว — native app ต้อง native จริง |
| **Avalonia** | ดี แต่ ecosystem เล็กกว่า WPF, ไม่มี FlowDocument equivalent |
| **WinUI 3** | Packaging complexity, limited FlowDocument, ยัง unstable ใน some scenarios |

---

## 4. System Architecture (C4 Model)

### 4.1 Context Level

```
┌─────────────────────────────────────────────────────┐
│                    User's Windows PC                 │
│                                                     │
│  ┌─────────────┐     ┌──────────────────────────┐   │
│  │ SRAN Desktop │────▶│ Local File System        │   │
│  │   (WPF)     │     │ Browser, Clipboard, Shell│   │
│  └──────┬──────┘     └──────────────────────────┘   │
│         │ MCP tools execute here                    │
└─────────┼───────────────────────────────────────────┘
          │ HTTPS
          ▼
┌─────────────────────────────────────────────────────┐
│              SRAN AI Gateway (Cloud)                 │
│                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Gateway   │  │chat-edge-api │  │ DLP Pipeline │  │
│  │ (Go:8080)│  │  (Go:4000)   │  │ (Py+Rust)    │  │
│  └──────────┘  └──────────────┘  └──────────────┘  │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Backend  │  │  PostgreSQL  │  │    Redis     │  │
│  │(FastAPI) │  │              │  │              │  │
│  └──────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 4.2 Container Level — Desktop App Internal

```
┌─────────────────────────────────────────────────────────────┐
│                     SRAN Desktop Process                     │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Presentation Layer (WPF/XAML)           │    │
│  │  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────┐  │    │
│  │  │ChatView  │ │Sidebar   │ │AgentPanel │ │Tray  │  │    │
│  │  │+Markdown │ │+History  │ │+ToolLog   │ │Icon  │  │    │
│  │  └────┬─────┘ └────┬─────┘ └─────┬─────┘ └──┬───┘  │    │
│  └───────┼────────────┼─────────────┼───────────┼──────┘    │
│          ▼            ▼             ▼           ▼           │
│  ┌─────────────────────────────────────────────────────┐    │
│  │            ViewModel Layer (MVVM)                    │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐    │    │
│  │  │ChatVM    │ │HistoryVM │ │AgentExecutionVM  │    │    │
│  │  └────┬─────┘ └────┬─────┘ └────────┬─────────┘    │    │
│  └───────┼────────────┼────────────────┼───────────────┘    │
│          ▼            ▼                ▼                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Application Services                    │    │
│  │  ┌───────────┐ ┌──────────┐ ┌──────────────────┐   │    │
│  │  │ChatService│ │SyncSvc   │ │AgentOrchestrator │   │    │
│  │  │(SSE parse)│ │(history) │ │  (tool loop)     │   │    │
│  │  └─────┬─────┘ └────┬─────┘ └────────┬─────────┘   │    │
│  └────────┼────────────┼────────────────┼──────────────┘    │
│           ▼            ▼                ▼                   │
│  ┌────────────────────────────────────────────────────┐     │
│  │              Infrastructure Layer                   │     │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │     │
│  │  │HttpClient│ │SQLite    │ │MCP Tool Host     │   │     │
│  │  │+SSE      │ │Cache     │ │(File,Browser,    │   │     │
│  │  │          │ │          │ │ Clipboard,Shell) │   │     │
│  │  └──────────┘ └──────────┘ └──────────────────┘   │     │
│  └────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 Component Breakdown

#### Presentation (WPF/XAML)

| Component | Responsibility |
|---|---|
| `MainWindow` | Shell: sidebar + content area + title bar |
| `ChatView` | Message list, input box, file drop zone |
| `MessageBubble` | Single message: markdown render + copy + feedback |
| `MarkdownRenderer` | Markdig pipeline → WPF FlowDocument with code highlighting |
| `StreamingIndicator` | Typing animation during SSE stream |
| `AgentPanel` | Tool execution log: what AI is doing, permission prompts |
| `ToolConsentDialog` | "AI wants to read C:\Users\..." — Allow / Deny / Always Allow |
| `FilePreview` | Image thumbnail, PDF preview, code preview |
| `SidebarView` | Conversation list, folders, search, new chat |
| `SystemTrayIcon` | Minimize to tray, quick chat hotkey |
| `SettingsView` | Server URL, auth, MCP tool toggles, theme |

#### ViewModels (CommunityToolkit.Mvvm)

| ViewModel | Key State & Commands |
|---|---|
| `ChatViewModel` | `Messages`, `InputText`, `IsStreaming`, `SendCommand`, `CancelCommand`, `AttachFileCommand` |
| `ConversationListViewModel` | `Conversations`, `ActiveConversation`, `CreateNewCommand`, `SearchCommand` |
| `AgentExecutionViewModel` | `ToolCalls`, `PendingConsent`, `ApproveCommand`, `DenyCommand`, execution history |
| `SettingsViewModel` | `ServerUrl`, `Theme`, `McpToolToggles`, `SaveCommand` |

#### Application Services

| Service | Responsibility |
|---|---|
| `ChatService` | Send message, parse SSE stream, accumulate content, emit IObservable events |
| `ConversationSyncService` | Sync conversations/messages between server and local SQLite |
| `AgentOrchestrator` | **Core agent loop**: detect tool_calls in LLM response → resolve MCP tool → request consent → execute → send result back → repeat until final answer |
| `FileService` | Upload files to chat-edge-api, manage local file references |
| `AuthService` | Login flow, token storage (DPAPI-encrypted), refresh |
| `NotificationService` | Toast notifications for mentions, completed tasks |

#### Infrastructure

| Component | Responsibility |
|---|---|
| `GatewayApiClient` | HTTP client for `/v1/chat/completions` with tool_use support |
| `ChatEdgeApiClient` | HTTP client for `/api/v1/` (conversations, files, history) |
| `SseStreamReader` | Custom `ReadableStream` → `IAsyncEnumerable<SseFrame>` parser |
| `SqliteConversationStore` | Local SQLite: conversations, messages, file metadata |
| `McpToolHost` | MCP tool registry + execution sandbox |
| `CredentialVault` | Windows DPAPI for token/key storage |

---

## 5. Dual-API Strategy — The Key Architecture Decision

นี่คือ decision ที่สำคัญที่สุดของ design นี้:

### ปัญหา
- **chat-edge-api** มี conversation management, files, RAG, voice, history ครบ — แต่ **ไม่ส่ง tool_calls กลับมาที่ client** (tools execute server-side)
- **gateway** `/v1/chat/completions` ส่ง tool_calls กลับมาครบ — แต่ **stateless**, client ต้อง manage history เอง

### Solution: Dual-API

```
                    Chat Mode                    Agent Mode
                    ─────────                    ──────────
User types ──▶ chat-edge-api              gateway /v1/chat/completions
               /api/v1/conversations       + tools[] in request
               /messages                   
                                           ◀── tool_calls in response
Server manages                             │
conversation history,                      ▼
RAG, voice, search                    MCP Tool Host (local)
                                           │
                                           ▼
                                      Execute on desktop
                                      (file read, browser, etc.)
                                           │
                                           ▼
                                      Send tool result back
                                      as role:"tool" message
                                           │
                                           ▼
                                      Loop until finish_reason="stop"
```

**Chat Mode** (default): ใช้ chat-edge-api เหมือน web-chat — server จัดการ history, files, RAG, voice ให้หมด

**Agent Mode** (เมื่อ user enable tools): ใช้ gateway API โดยตรง — desktop client จัดการ tool_use loop เอง, conversation history อยู่ใน local SQLite, tool execution อยู่ฝั่ง client ทั้งหมด

**ทำไม ไม่แก้ chat-edge-api ให้ forward tool_calls?**  
เพราะ tool execution ต้องเกิดบน user's machine — ต่อให้ server forward tool_call มา client ก็ต้อง execute แล้วส่งกลับ ซึ่งต้อง WebSocket หรือ long-poll ที่ chat-edge-api ไม่มี Gateway API + stateless loop เรียบง่ายกว่าและ decoupled กว่ามาก

---

## 6. MCP Agent Architecture — The Differentiator

### 6.1 What is MCP?

Model Context Protocol คือ open standard ที่ให้ AI models เรียก tools ที่ run ใน environment อื่นได้ Desktop app จะ implement MCP **host** ที่ register tools แล้ว expose ให้ LLM ผ่าน `tools[]` parameter ของ OpenAI-compatible API

### 6.2 Tool Registry

```csharp
// Conceptual interface — actual implementation below
public interface IMcpTool
{
    string Name { get; }
    string Description { get; }
    JsonElement ParametersSchema { get; }    // JSON Schema
    ToolCategory Category { get; }           // FileSystem, Browser, Shell, Clipboard
    PermissionLevel DefaultPermission { get; } // Ask, AllowOnce, AllowAlways, Deny
    
    Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct);
}
```

### 6.3 Built-in Tools (Phase 1)

| Tool | Category | Description | Permission |
|---|---|---|---|
| `read_file` | FileSystem | อ่าน file content (text/binary) | Ask — แสดง path ให้ user เห็น |
| `write_file` | FileSystem | เขียน/สร้าง file | Ask — แสดง path + preview |
| `list_directory` | FileSystem | list files ใน directory | AllowOnce per directory |
| `search_files` | FileSystem | grep/glob search | AllowOnce per scope |
| `open_url` | Browser | เปิด URL ใน default browser | Ask — แสดง URL |
| `read_clipboard` | Clipboard | อ่าน clipboard text | Ask |
| `write_clipboard` | Clipboard | เขียน clipboard | Ask — แสดง content |
| `run_command` | Shell | Execute shell command | Ask — แสดง command, timeout 30s |
| `screenshot` | Screen | Capture screenshot (full/window) | Ask — แสดง scope |

### 6.4 Agent Execution Loop

```
┌──────────────────────────────────────────────────────┐
│                Agent Orchestrator                     │
│                                                      │
│  1. User sends message                               │
│     ▼                                                │
│  2. Build request:                                   │
│     { messages: [...history, user_msg],              │
│       tools: [registered_mcp_tools],                 │
│       model: selected_model }                        │
│     ▼                                                │
│  3. POST /v1/chat/completions (stream=true)          │
│     ▼                                                │
│  4. Parse SSE stream                                 │
│     ├── content_delta → append to UI (streaming)     │
│     ├── tool_calls → go to step 5                    │
│     └── finish_reason=stop → DONE                    │
│     ▼                                                │
│  5. For each tool_call:                              │
│     a. Resolve tool from registry                    │
│     b. Check permission policy                       │
│     c. If Ask → show ToolConsentDialog               │
│     d. If denied → return error result               │
│     e. Execute tool → collect result                 │
│     f. Show result in AgentPanel                     │
│     ▼                                                │
│  6. Append tool results as role:"tool" messages      │
│     ▼                                                │
│  7. Go to step 2 (loop until no more tool_calls)    │
│                                                      │
│  Safety: max 20 iterations, 5min total timeout       │
└──────────────────────────────────────────────────────┘
```

### 6.5 Permission Model — Three Tiers

```
┌─────────────────────────────────────────────────────────────┐
│                    Permission Tiers                          │
│                                                             │
│  Tier 1: SAFE (auto-allow, no prompt)                       │
│  ├── read_clipboard (text only)                             │
│  ├── list_directory (user's home subdirs only)              │
│  └── screenshot (with notification badge)                   │
│                                                             │
│  Tier 2: SENSITIVE (ask once per session)                   │
│  ├── read_file (within allowed directories)                 │
│  ├── search_files (within allowed scope)                    │
│  └── open_url (show URL first)                              │
│                                                             │
│  Tier 3: DANGEROUS (ask every time, show preview)           │
│  ├── write_file (show diff/content before write)            │
│  ├── write_clipboard                                        │
│  └── run_command (show exact command, sandboxed)             │
│                                                             │
│  User can override: promote or demote any tool              │
│  All decisions logged to audit trail                        │
└─────────────────────────────────────────────────────────────┘
```

### 6.6 Sandbox Constraints

| Constraint | Implementation |
|---|---|
| **File scope** | Tools can only access directories user explicitly allows via folder picker |
| **Shell timeout** | `run_command` has 30s hard timeout, no network access by default |
| **URL allowlist** | `open_url` checks against user-defined allowlist |
| **Token limit** | Tool results truncated to 50K chars to avoid context overflow |
| **Iteration cap** | Agent loop stops after 20 tool calls or 5 minutes |
| **Kill switch** | User can press ESC or click Stop at any point → cancels current tool + stream |

---

## 7. Data Flow Diagrams

### 7.1 Chat Mode — Normal Conversation

```
User              Desktop App             chat-edge-api          Gateway         Provider
 │                    │                        │                    │               │
 │ type message       │                        │                    │               │
 │───────────────────▶│                        │                    │               │
 │                    │ POST /api/v1/          │                    │               │
 │                    │ conversations/{id}/     │                    │               │
 │                    │ messages               │                    │               │
 │                    │───────────────────────▶│                    │               │
 │                    │                        │ POST /v1/chat/     │               │
 │                    │                        │ completions        │               │
 │                    │                        │───────────────────▶│               │
 │                    │                        │                    │──────────────▶│
 │                    │                        │                    │◀──────────────│
 │                    │ SSE: message_start     │◀───────────────────│               │
 │                    │◀───────────────────────│                    │               │
 │ streaming text     │ SSE: content_delta ×N  │                    │               │
 │◀───────────────────│◀───────────────────────│                    │               │
 │                    │ SSE: message_complete   │                    │               │
 │                    │◀───────────────────────│                    │               │
 │                    │                        │                    │               │
 │                    │ cache to SQLite        │                    │               │
 │                    │──────────┐             │                    │               │
 │                    │          │             │                    │               │
 │                    │◀─────────┘             │                    │               │
```

### 7.2 Agent Mode — Tool Execution Loop

```
User              Desktop App              Gateway              Provider
 │                    │                       │                     │
 │ "อ่านไฟล์ report   │                       │                     │
 │  แล้วสรุปให้"       │                       │                     │
 │───────────────────▶│                       │                     │
 │                    │ POST /v1/chat/        │                     │
 │                    │ completions           │                     │
 │                    │ { tools: [read_file,  │                     │
 │                    │   write_file, ...],   │                     │
 │                    │   messages: [...] }   │                     │
 │                    │──────────────────────▶│                     │
 │                    │                       │────────────────────▶│
 │                    │                       │◀────────────────────│
 │                    │ SSE: tool_calls       │                     │
 │                    │ [{name:"read_file",   │                     │
 │                    │   args:{path:"..."}}] │                     │
 │                    │◀──────────────────────│                     │
 │                    │                       │                     │
 │ ┌──────────────────┤                       │                     │
 │ │ ToolConsentDialog│                       │                     │
 │ │ "AI ต้องการอ่าน   │                       │                     │
 │ │  C:\report.xlsx" │                       │                     │
 │ │ [Allow] [Deny]   │                       │                     │
 │ └──────────────────┤                       │                     │
 │ clicks Allow       │                       │                     │
 │───────────────────▶│                       │                     │
 │                    │ execute read_file     │                     │
 │                    │ locally               │                     │
 │                    │──────────┐            │                     │
 │                    │          │            │                     │
 │                    │◀─────────┘            │                     │
 │                    │ file content          │                     │
 │                    │                       │                     │
 │                    │ POST /v1/chat/        │                     │
 │                    │ completions           │                     │
 │                    │ { messages: [         │                     │
 │                    │   ...prev,            │                     │
 │                    │   {role:"assistant",  │                     │
 │                    │    tool_calls:[...]}, │                     │
 │                    │   {role:"tool",       │                     │
 │                    │    content:"..."}     │                     │
 │                    │ ]}                    │                     │
 │                    │──────────────────────▶│                     │
 │                    │                       │────────────────────▶│
 │                    │ SSE: content_delta    │◀────────────────────│
 │ streaming summary  │◀──────────────────────│                     │
 │◀───────────────────│                       │                     │
 │                    │ finish_reason=stop    │                     │
 │                    │◀──────────────────────│                     │
```

### 7.3 File Upload Flow

```
User              Desktop App           chat-edge-api
 │                    │                      │
 │ drag & drop file   │                      │
 │───────────────────▶│                      │
 │                    │ validate file type    │
 │                    │ + size (<25MB)        │
 │                    │                      │
 │                    │ XHR POST /api/v1/    │
 │                    │ conversations/{id}/   │
 │                    │ files                 │
 │                    │ (multipart/form-data) │
 │                    │──────────────────────▶│
 │ progress bar       │                      │
 │◀───────────────────│                      │
 │                    │ { id, name, type,    │
 │                    │   size, url }        │
 │                    │◀──────────────────────│
 │                    │                      │
 │                    │ attach file_id to    │
 │                    │ next sendMessage()   │
```

---

## 8. Security Model

### 8.1 Authentication Flow

```
┌────────────────────────────────────────────────────┐
│                Desktop Auth Flow                    │
│                                                    │
│  1. User opens app → sees login screen             │
│  2. User enters email + password                   │
│     (or clicks "Login with portal")                │
│                                                    │
│  3. App calls POST /portal/auth/login              │
│     → receives { access_token, refresh_token }     │
│                                                    │
│  4. Tokens stored via Windows DPAPI                │
│     (encrypted to current user's credential)       │
│                                                    │
│  5. All API calls:                                 │
│     Authorization: Bearer {access_token}           │
│                                                    │
│  6. On 401 → refresh via refresh_token             │
│     → if refresh fails → back to login             │
│                                                    │
│  Note: NO plain-text token storage ever.           │
│  DPAPI = OS-level encryption tied to Windows login │
└────────────────────────────────────────────────────┘
```

### 8.2 Threat Model

| Threat | Mitigation |
|---|---|
| **Token theft** | DPAPI encryption; tokens never written to disk as plaintext |
| **MCP tool abuse** | Permission tiers; consent dialog shows exact args before execution |
| **Prompt injection via tool results** | Truncate results to 50K chars; strip control characters; LLM system prompt includes "tool results are untrusted data" |
| **Directory traversal** | Allowlisted directories only; `Path.GetFullPath` canonicalization; reject symlinks outside scope |
| **Shell injection** | `run_command` uses `ProcessStartInfo` with argument array (no shell interpretation); blocklist for rm -rf, format, etc. |
| **DLP bypass** | Messages still go through gateway → DLP pipeline; tool results are user-local data, not sent to other users |
| **Man-in-middle** | HTTPS only; certificate pinning optional for enterprise |

### 8.3 Data at Rest

| Data | Location | Encryption |
|---|---|---|
| Auth tokens | Windows Credential Manager via DPAPI | OS-level |
| Chat history cache | `%APPDATA%\SRAN Desktop\cache.db` (SQLite) | SQLite encryption extension (optional, enterprise) |
| MCP tool permissions | `%APPDATA%\SRAN Desktop\permissions.json` | Plaintext (non-sensitive — just allow/deny flags) |
| Logs | `%APPDATA%\SRAN Desktop\logs\` | Plaintext, auto-rotate 7 days |

---

## 9. Local Storage Schema (SQLite)

```sql
-- Conversations (synced from chat-edge-api, also stores agent-mode local convos)
CREATE TABLE conversations (
    id              TEXT PRIMARY KEY,
    title           TEXT,
    model_id        TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_pinned       INTEGER DEFAULT 0,
    folder_id       TEXT,
    source          TEXT NOT NULL CHECK(source IN ('server','local')),
    -- 'server' = synced from chat-edge-api
    -- 'local'  = agent-mode, managed locally
    last_synced_at  TEXT
);

-- Messages
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
    content         TEXT,
    tool_calls      TEXT,  -- JSON array of tool_call objects
    tool_call_id    TEXT,
    name            TEXT,  -- tool name for role=tool
    model_id        TEXT,
    token_usage     TEXT,  -- JSON {prompt_tokens, completion_tokens, ...}
    created_at      TEXT NOT NULL,
    sort_order      INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- Files (uploaded or locally referenced)
CREATE TABLE files (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    name            TEXT NOT NULL,
    mime_type       TEXT,
    size_bytes      INTEGER,
    local_path      TEXT,      -- for locally referenced files
    server_url      TEXT,      -- for uploaded files
    created_at      TEXT NOT NULL
);

-- MCP Tool Audit Log
CREATE TABLE tool_executions (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_id      TEXT,
    tool_name       TEXT NOT NULL,
    arguments       TEXT NOT NULL,  -- JSON
    result          TEXT,           -- JSON (truncated)
    permission      TEXT NOT NULL CHECK(permission IN ('auto','allowed','denied')),
    duration_ms     INTEGER,
    error           TEXT,
    executed_at     TEXT NOT NULL
);

-- Permission Overrides
CREATE TABLE tool_permissions (
    tool_name       TEXT NOT NULL,
    scope           TEXT NOT NULL,  -- e.g., 'C:\Users\Beat\Documents' or '*'
    permission      TEXT NOT NULL CHECK(permission IN ('allow_always','deny_always','ask')),
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (tool_name, scope)
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, sort_order);
CREATE INDEX idx_tool_executions_conversation ON tool_executions(conversation_id);
```

---

## 10. SSE Stream Parser — Core Implementation Design

```csharp
// Key abstraction: IAsyncEnumerable-based SSE parser
// Mirrors web-chat's parseSSE() but in idiomatic C#

public record SseFrame(string? Event, string Data);

public static class SseStreamReader
{
    public static async IAsyncEnumerable<SseFrame> ParseAsync(
        Stream stream, 
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        using var reader = new StreamReader(stream, Encoding.UTF8);
        var sb = new StringBuilder();
        string? eventType = null;

        while (!ct.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(ct);
            if (line == null) yield break;           // stream closed

            if (line.Length == 0)                     // blank line = frame boundary
            {
                if (sb.Length > 0)
                {
                    yield return new SseFrame(eventType, sb.ToString());
                    sb.Clear();
                    eventType = null;
                }
                continue;
            }

            if (line.StartsWith("event:"))
                eventType = line[6..].Trim();
            else if (line.StartsWith("data:"))
            {
                if (sb.Length > 0) sb.Append('\n');
                sb.Append(line[5..].TrimStart());
            }
            // ignore id:, retry:, comments
        }
    }
}

// Usage in ChatService:
await foreach (var frame in SseStreamReader.ParseAsync(responseStream, ct))
{
    if (frame.Data == "[DONE]") break;
    
    var chunk = JsonSerializer.Deserialize<ChatCompletionChunk>(frame.Data);
    
    if (chunk.Choices[0].Delta.ToolCalls is { } toolCalls)
        await _agentOrchestrator.HandleToolCallsAsync(toolCalls, ct);
    else if (chunk.Choices[0].Delta.Content is { } content)
        OnContentDelta?.Invoke(content);  // → UI update via dispatcher
}
```

---

## 11. Markdown Rendering Pipeline

```
Raw Markdown (from LLM)
    │
    ▼
┌─────────────────────────────┐
│  Markdig Pipeline            │
│  ├── GFM (tables, task list)│
│  ├── SyntaxHighlighting     │
│  ├── MathBlock (LaTeX)      │
│  └── Custom: CodeBlock      │
│       with copy button      │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  MarkdownToFlowDocument     │
│  (custom WPF renderer)      │
│  ├── Heading → Run (bold)   │
│  ├── Paragraph → Paragraph  │
│  ├── Code → RichTextBox     │
│  │   with syntax colors     │
│  ├── Table → WPF Table      │
│  ├── Image → inline BitmapImage │
│  └── Link → Hyperlink       │
└──────────┬──────────────────┘
           │
           ▼
    FlowDocument (WPF native)
    rendered in RichTextBox
    with smooth scrolling

    Special: during streaming,
    FlowDocument is updated
    incrementally (not rebuilt)
    using RAF-equivalent 
    DispatcherTimer at 60fps
```

---

## 12. Project Structure

```
SranDesktop/
├── SranDesktop.sln
│
├── src/
│   ├── SranDesktop.App/                    # WPF Application (startup, DI, config)
│   │   ├── App.xaml / App.xaml.cs
│   │   ├── appsettings.json
│   │   └── Hosting/
│   │       └── AppHostBuilder.cs           # Microsoft.Extensions.Hosting setup
│   │
│   ├── SranDesktop.Presentation/           # Views + ViewModels (WPF-specific)
│   │   ├── Views/
│   │   │   ├── MainWindow.xaml
│   │   │   ├── ChatView.xaml
│   │   │   ├── SidebarView.xaml
│   │   │   ├── AgentPanelView.xaml
│   │   │   ├── SettingsView.xaml
│   │   │   ├── LoginView.xaml
│   │   │   └── Dialogs/
│   │   │       └── ToolConsentDialog.xaml
│   │   ├── ViewModels/
│   │   │   ├── MainViewModel.cs
│   │   │   ├── ChatViewModel.cs
│   │   │   ├── ConversationListViewModel.cs
│   │   │   ├── AgentExecutionViewModel.cs
│   │   │   └── SettingsViewModel.cs
│   │   ├── Controls/
│   │   │   ├── MarkdownRenderer.cs         # Markdig → FlowDocument
│   │   │   ├── StreamingTextBlock.cs
│   │   │   └── FileDropZone.cs
│   │   ├── Converters/
│   │   └── Resources/
│   │       ├── Themes/
│   │       │   ├── Light.xaml
│   │       │   └── Dark.xaml
│   │       └── Icons/
│   │
│   ├── SranDesktop.Application/            # Application services (no UI dependency)
│   │   ├── Services/
│   │   │   ├── ChatService.cs
│   │   │   ├── AgentOrchestrator.cs
│   │   │   ├── ConversationSyncService.cs
│   │   │   ├── FileService.cs
│   │   │   ├── AuthService.cs
│   │   │   └── NotificationService.cs
│   │   ├── Interfaces/
│   │   │   ├── IChatService.cs
│   │   │   ├── IAgentOrchestrator.cs
│   │   │   └── IMcpToolHost.cs
│   │   └── Models/
│   │       ├── ChatCompletionChunk.cs
│   │       ├── SseFrame.cs
│   │       └── ToolCall.cs
│   │
│   ├── SranDesktop.Domain/                 # Domain models (pure C#, no dependencies)
│   │   ├── Entities/
│   │   │   ├── Conversation.cs
│   │   │   ├── Message.cs
│   │   │   ├── FileAttachment.cs
│   │   │   └── ToolExecution.cs
│   │   ├── Enums/
│   │   │   ├── MessageRole.cs
│   │   │   ├── PermissionLevel.cs
│   │   │   └── ToolCategory.cs
│   │   └── ValueObjects/
│   │       └── ConversationId.cs
│   │
│   ├── SranDesktop.Infrastructure/         # External integrations
│   │   ├── Api/
│   │   │   ├── GatewayApiClient.cs
│   │   │   ├── ChatEdgeApiClient.cs
│   │   │   └── SseStreamReader.cs
│   │   ├── Storage/
│   │   │   ├── SqliteConversationStore.cs
│   │   │   └── CredentialVault.cs          # DPAPI wrapper
│   │   └── Mcp/
│   │       ├── McpToolHost.cs
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
│   └── SranDesktop.Tests/                  # Unit + Integration tests
│       ├── Services/
│       ├── Mcp/
│       └── Infrastructure/
│
├── build/
│   ├── SranDesktop.iss                     # Inno Setup installer script
│   └── ci.yml                              # GitHub Actions CI
│
└── docs/
    └── ARCHITECTURE.md                     # This document
```

---

## 13. Phase Roadmap

### Phase 1: Foundation + Chat (4 weeks)

**Goal:** Functional chat client ที่ทำงานเหมือน web-chat ได้

| Week | Deliverable |
|---|---|
| 1 | Project scaffold, DI, auth flow (login → token → DPAPI), basic MainWindow shell |
| 2 | ChatView + SSE streaming (content_delta only), message list with basic text rendering |
| 3 | Markdown rendering (Markdig → FlowDocument), code highlighting, conversation list/switch |
| 4 | File upload (drag & drop + button), conversation sync, search, dark/light theme |

**Exit Criteria:** User can login, chat with streaming, upload files, switch conversations, see markdown properly.

### Phase 2: Agent + MCP (4 weeks)

**Goal:** AI สามารถเรียก tools บน desktop ได้ — นี่คือ differentiator

| Week | Deliverable |
|---|---|
| 5 | Dual-API mode switch, gateway client with tool_use, AgentOrchestrator loop |
| 6 | MCP Tool Host + first 4 tools (read_file, write_file, list_directory, search_files) |
| 7 | Permission system (consent dialog, tiers, audit log), browser + clipboard tools |
| 8 | run_command tool (sandboxed), screenshot tool, AgentPanel UI (tool execution log) |

**Exit Criteria:** User can ask AI to "อ่านไฟล์ report.xlsx แล้วสรุป" and it actually works — reads the file, shows consent, returns summary.

### Phase 3: Polish + Ship (2 weeks)

| Week | Deliverable |
|---|---|
| 9 | System tray, global hotkey (Ctrl+Shift+Space), auto-start, installer (Inno Setup) |
| 10 | Performance tuning (streaming FPS, memory), error handling edge cases, UAT testing |

**Exit Criteria:** Installable .exe, first-run experience ดี, ไม่ crash ใน normal usage.

### Phase 4: Advanced (Future)

- Voice input/output (Whisper proxy + TTS)
- RAG integration (knowledge base management ใน desktop)
- Plugin system (user-defined MCP tools via DLLs or scripts)
- Auto-update (Squirrel or similar)
- Multi-window / detach conversations
- Screen sharing — AI sees what you see (periodic screenshot + vision API)

---

## 14. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **WPF FlowDocument perf** กับ long conversations (10K+ messages) | UI lag | Virtualize: only render visible messages + buffer; lazy-load markdown rendering |
| **SSE disconnects** (network flaky) | Lost streaming | Auto-reconnect with exponential backoff; partial content preserved locally |
| **MCP tool escapes sandbox** | Security breach | Defense in depth: allowlisted dirs, process isolation for shell, audit log |
| **Gateway API changes** break desktop | Client crash | Version negotiation header; feature flags; graceful degradation |
| **DLP not applied to agent mode** | Data leak | Agent mode messages still go through gateway → DLP pipeline; tool results are local-only |
| **Large file in tool result** overflows context | Token waste | Hard truncate at 50K chars; binary files → base64 only first 100KB; warn user |

---

## 15. Metrics & Observability

| Metric | How |
|---|---|
| **Streaming latency** (time to first token) | Client-side timer: send → first content_delta |
| **Tool execution duration** | Per-tool timing in tool_executions table |
| **Error rate** | Local Serilog → structured JSON; optionally ship to server |
| **Conversation count** (local vs server) | SQLite query on app start |
| **Permission deny rate** | Count from tool_executions where permission=denied |

---

## 16. Comparison with Web-Chat

| Feature | Web-Chat | Desktop App |
|---|---|---|
| Chat + streaming | ✅ | ✅ |
| File upload | ✅ | ✅ |
| Conversation history | ✅ (server) | ✅ (server + local cache) |
| Markdown rendering | ✅ (react-markdown) | ✅ (Markdig → FlowDocument) |
| RAG / Knowledge base | ✅ | Phase 4 |
| Voice | ✅ | Phase 4 |
| **MCP Agent (tool execution)** | ❌ impossible | ✅ Phase 2 |
| **File system access** | ❌ impossible | ✅ Phase 2 |
| **Shell execution** | ❌ impossible | ✅ Phase 2 |
| **Screenshot + vision** | ❌ impossible | ✅ Phase 2 |
| **System tray + hotkey** | ❌ | ✅ Phase 3 |
| **Offline history** | ❌ | ✅ Phase 1 |
| Works on mobile | ✅ (responsive) | ❌ (Windows only) |

---

## Appendix A: SSE Event Types Reference

### Gateway `/v1/chat/completions` (Agent Mode)

| Frame | JSON Key | Description |
|---|---|---|
| Content delta | `choices[0].delta.content` | Incremental text |
| Tool call | `choices[0].delta.tool_calls[].function.{name,arguments}` | Tool invocation |
| Finish | `choices[0].finish_reason` = `"stop"` or `"tool_calls"` | Stream end |
| Reasoning | `type:"reasoning_summary_delta"` + `text` | Extended thinking |
| Error | `error.{code,message}` | Provider/gateway error |
| Fallback | `type:"fallback_info"` | Model fallback notification |
| Done | `data: [DONE]` | Final sentinel |

### chat-edge-api `/api/v1/` (Chat Mode)

| Event Name | Description |
|---|---|
| `message_start` | `{ message_id, model_id }` |
| `content_delta` | `{ text }` |
| `reasoning_delta` | `{ text }` |
| `analysis_status` | `{ status: "started"\|"completed" }` |
| `image_generating` | `{ status, prompt }` |
| `search_status` | `{ status, query }` |
| `message_sources` | `[{ title, url, snippet }]` |
| `message_files` | `[{ id, name, url, type }]` |
| `message_complete` | `{ token_usage, response_time_ms }` |
| `fallback_info` | `{ requested_model, actual_model, reason }` |
| `error` | `{ code, message }` |

---

## Appendix B: API Endpoints Used by Desktop

### From chat-edge-api (Chat Mode)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/auth/login` | Login |
| POST | `/api/v1/auth/refresh` | Token refresh |
| GET | `/api/v1/conversations` | List conversations |
| POST | `/api/v1/conversations` | Create conversation |
| DELETE | `/api/v1/conversations/:id` | Delete conversation |
| PATCH | `/api/v1/conversations/:id` | Rename/pin |
| GET | `/api/v1/conversations/:id/messages` | Load messages |
| POST | `/api/v1/conversations/:id/messages` | Send message (SSE) |
| POST | `/api/v1/conversations/:id/messages/:mid/regenerate` | Regenerate (SSE) |
| POST | `/api/v1/conversations/:id/files` | Upload file |
| GET | `/api/v1/models` | List available models |
| GET | `/api/v1/folders` | List folders |
| POST | `/api/v1/folders` | Create folder |

### From Gateway (Agent Mode)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/chat/completions` | Chat + tool_use (SSE) |
| GET | `/v1/models` | List models |

---

*End of Architecture Document*
