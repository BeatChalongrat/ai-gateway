# SRAN Desktop — Architecture

Native Windows chat client (C# / .NET 8 / WPF) for the SRAN AI Gateway.
Build spec: `../../docs/DESKTOP_BUILD_PROMPT.md` — **with the auth corrections below**.

## Layers

```
SranDesktop.App            WinExe. Generic Host + DI (AppHostBuilder), Serilog file logs,
                           appsettings.json defaults, applies saved theme at startup.
SranDesktop.Presentation   Views (XAML) + ViewModels (CommunityToolkit.Mvvm) + Controls
                           (MarkdownRenderer/MarkdownViewer/MessageBubble) + ThemeManager.
SranDesktop.Application    Services (Auth, Chat, ConversationSync, File, TokenSession),
                           interfaces, DTOs/events. No UI or Windows dependencies.
SranDesktop.Domain         Entities + enums + TokenUsage. Zero dependencies.
SranDesktop.Infrastructure PortalAuthClient, ChatEdgeApiClient, AuthTokenHandler,
                           SseStreamReader, SqliteConversationStore, DpapiCredentialStore,
                           JsonSettingsService. net8.0-windows (DPAPI).
SranDesktop.Tests          xunit + Moq + FluentAssertions. Live tests gated by SRAN_LIVE=1.
```

## Auth — differs from the original build prompt

The chat-edge `/api/v1/auth/*` endpoints are **410 stubs**. Identity is owned by the
FastAPI backend behind the gateway (:8080):

- `POST /portal/auth/login {email,password}` → tokens arrive **only as HttpOnly
  cookies** (`portal_access_token`, `portal_refresh_token`); body = `{"user":{...}}`.
  Auth failures may return **HTTP 200 with an error envelope and no cookies**.
- `POST /portal/auth/refresh {refresh_token}` (body fallback for non-browser clients)
  rotates the pair.
- chat-edge (:4000) accepts the portal access JWT as `Authorization: Bearer`.

`PortalAuthClient` (UseCookies=false) harvests tokens from Set-Cookie headers;
`TokenSession` persists them via `DpapiCredentialStore`
(%APPDATA%\SRAN Desktop\credentials.dat, DPAPI CurrentUser); `AuthTokenHandler`
attaches Bearer to every chat-edge call and does a single-flight refresh + one retry
on 401.

## Chat streaming

`POST /api/v1/conversations/{id}/messages` body `{content, stream:true, file_ids?}` →
named SSE events. Wire notes that differ from the build prompt:

- `content_delta` / `reasoning_delta` payloads are `{"delta":"…"}` (not `text`)
- `message_complete.token_usage` is `{input, output}` and currently absent
- history endpoint returns `{has_more, messages[]}`: latest N ascending, `before` pages older

Flow: `ChatEdgeApiClient.StreamMessageAsync` (HttpCompletionOption.ResponseHeadersRead,
infinite client timeout, per-call deadlines on non-streaming calls) → `SseStreamReader`
frames → `ChatService` maps to typed `ChatStreamEvent`s → `ChatViewModel` updates
`MessageViewModel` (StringBuilder append) → `MarkdownViewer` re-renders throttled
(~66 ms) via `MarkdownRenderer` (Markdig AST → WPF elements, DynamicResource colors
so theme switches restyle rendered messages live).

## Local persistence

SQLite at %APPDATA%\SRAN Desktop\data.db (schema per the build prompt: conversations,
messages, files, tool_executions, tool_permissions). Server conversations are synced
into it by `ConversationSyncService` (page → upsert → reconcile deletions only after a
complete fetch; `source='local'` rows are never touched — reserved for Agent Mode).
Chat-mode *messages* are not cached locally; the server is the source of truth.

## Settings & theming

`JsonSettingsService` (%APPDATA%\SRAN Desktop\settings.json) overrides appsettings
defaults for server URLs / default model / theme. `ThemeManager` swaps the merged
theme ResourceDictionary at runtime; all views bind colors with DynamicResource.
Server URL changes require an app restart (HttpClient BaseAddress fixed at startup).

## Phase 2 — Agent Mode (built + verified live)

The 🤖 toggle in the composer switches a turn to the client-driven tool loop:

- **Auth (differs from the build prompt):** `/v1/chat/completions` REJECTS portal
  JWTs (`get_resolved_api_key` validates gw_ API keys / admin JWTs only). The app
  self-provisions a `gw_…` key once via `POST /portal/projects/{id}/keys`
  (portal-JWT-authenticated; raw key returned exactly once) — `AgentKeyProvider`
  caches it in the DPAPI store; `GatewayApiClient` sends it as Bearer.
- **Loop** (`AgentOrchestrator`, max 20 iterations / 5 min): stream completion →
  `GatewayChunkParser` accumulates OpenAI-format chunks (handles both streamed
  argument fragments and the gateway's DLP-buffered single-shot arguments) →
  on `finish_reason:"tool_calls"` execute each call → append assistant+tool
  messages → repeat until `stop`.
- **DLP marker:** the gateway can replace tool arguments with
  `{"_dlp_blocked":true,…}` (tool_arg_policy.py: unknown tools with masked args
  are blocked). The orchestrator detects the marker, skips execution, and
  returns an explanatory tool result so the model can recover.
- **Permissions:** persisted `tool_permissions` (allow/deny always, per
  tool+scope) → session cache (Allow Once) → tool default. Consent dialog
  (Deny / Allow Once / Always Allow) raised via an async event; no UI handler
  ⇒ deny. Every execution is logged to `tool_executions`.
- **Tools (Infrastructure/Mcp/Tools):** read_file, write_file, list_directory,
  search_files, open_url, read_clipboard, write_clipboard, run_command
  (cmd/powershell, 30s timeout, blocklist), screenshot. File tools are confined
  by `PathGuard` to the user profile + temp; clipboard hops to the WPF
  dispatcher (STA); the agent system prompt tells the model the real home dir.
- Agent turns are NOT synced to chat-edge; history is built from the in-memory
  message list and tool executions live in local SQLite only.

## Phase 3 — Desktop integration + packaging (built + verified)

- **App icon**: `Assets/app.ico` (multi-res PNG-in-ICO, "S" on accent blue),
  wired as `ApplicationIcon`, WPF `Resource`, window + dialog `Icon`, and tray.
- **Single instance** (`SingleInstance.cs`): mutex + named pipe; a 2nd launch
  forwards "activate" and exits, the 1st brings its window forward.
- **System tray** (`H.NotifyIcon.Wpf` `TaskbarIcon`, `ForceCreate(false)`):
  `ShutdownMode=OnExplicitShutdown`; closing the window minimizes to tray
  (respecting `MinimizeToTray`), first time shows a balloon via
  `ShowNotification(...)` — NOTE v2.1.4 has no `ShowBalloonTip`. Tray menu:
  Show / New Chat / Exit; double-click restores.
- **Global hotkey** (`GlobalHotkey.cs`): Win32 `RegisterHotKey` Ctrl+Shift+Space
  toggles the window; `EnsureHandle()` so it works while hidden; logs a warning
  (no crash) if the combo is already held.
- **Auto-start** (`AutoStartManager` + `IStartupManager`/`WindowsStartupManager`):
  HKCU Run key with `--minimized`; App.xaml.cs honors `--minimized` (build the
  window, stay in tray).
- **First run**: `ISettingsService.IsFirstRun` (settings.json absent) →
  `FirstRunDialog` (server URLs + tips + start-with-Windows) before the shell.
- **Settings**: new `MinimizeToTray` / `StartWithWindows` / `AgentModeDefault`.
- **Global exception handlers** (App): DispatcherUnhandledException (dialog +
  Handled=true), AppDomain.UnhandledException, TaskScheduler.UnobservedTaskException.
- **Performance**: message list is a UI-virtualized `ListBox` (Recycling, Pixel
  scroll) with a centered 860px column; auto-scroll rewired to the ListBox's
  internal ScrollViewer. Streaming shows plain text and swaps to markdown once
  (`MarkdownViewer.IsStreaming` skips parsing per-delta); UI refresh throttled
  to ~30fps. SSE responses are `using`/`await using` disposed. (SQLite write
  batching is N/A — chat history is server-owned, agent history is in-memory.)
- **Network resilience** (`RetryHelper`): exp-backoff retry of transient
  (408/429/5xx + connection) failures on idempotent GETs; gateway-unreachable
  and chat-edge-unreachable produce clear, actionable messages.
- **Packaging**: `Properties/PublishProfiles/win-x64.pubxml` (single-file,
  self-contained, compressed, NOT trimmed — WPF reflection); `build/SranDesktop.iss`
  (Inno Setup 6, per-user `PrivilegesRequired=lowest`, HKCU auto-start task,
  taskkill before install/uninstall). Verified: `dotnet publish` → 70 MB
  self-contained exe launches with no runtime; ISCC → 66 MB installer;
  silent install → launch → uninstall leaves nothing behind. 98 tests pass.

## Dev notes

- .NET SDK is user-local: `%LOCALAPPDATA%\Microsoft\dotnet` (set `DOTNET_ROOT` + PATH;
  the exe also needs `DOTNET_ROOT` to find the runtime).
- Live tests: `SRAN_LIVE=1 dotnet test` (uses UAT seed user somchai@acme.ai.gateway).
- `SRAN_SEED_SESSION=1` + the seed test logs in and fills the real credential store
  so the app starts signed-in.
