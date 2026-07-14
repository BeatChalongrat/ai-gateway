# DESKTOP FIX — PHASE 5: Backend wiring (cancel, fallback chip, feedback, context nudge, full-text search)

You are working on **SranDesktop** (Windows WPF, .NET 8, at `SranDesktop/`). **Desktop-only phase** — chat-edge and the gateway already expose everything needed (all endpoint shapes below are verified against the server code; do NOT modify Go or Python). Phases 1–4 are merged; do not regress them.

Two turn paths exist — keep them straight throughout:
- **Agent path** (default): gateway `/v1/chat/completions`, local ids, conversations `source='local'`, optionally mirrored server-side by Phase 3 sync (`conversations.server_id`).
- **Chat-edge path** (image/video mode): server-owned conversations/messages, SSE named events via `ChatService`, `_turnUsedChatEdge = true` (`ChatViewModel.cs:1021,1034`).

## Ground rules

- Read every file you touch IN FULL first. Complete the reconnaissance (R0) before coding and answer it in your summary.
- All new user-facing strings → BOTH `En`/`Th` in `Dict.cs` (`DictParityTests` enforces). Natural Thai.
- Every network call added here is **fire-and-forget with logging** — none of these features may ever block, delay, or fail a turn or the UI.
- Pure decision logic (nudge rule, search-hit mapping) goes in testable classes; tests required.
- Finish state: `dotnet build` 0/0; `dotnet test` all green.

## R0 — Mandatory reconnaissance (answer all in the summary)

1. `FallbackInfoEvent` in `SranDesktop.Application` (produced by `ChatService.MapFrame` — verified: chat-edge re-emits the SSE event `fallback_info` with the gateway's object verbatim: `{"requested_model","actual_model","reason","attempts":[...]}`): its exact record shape today.
2. Chat-edge path: where does the SERVER message id for the assistant message land in the ViewModel? (`StreamStartedEvent`/`StreamCompletedEvent` carry `message_id`; `MessageViewModel.Id` has a public setter — confirm it ends up holding the server id for chat-edge turns, and what user-message ids hold.)
3. `CancelStreaming` (`ChatViewModel.cs:1990` — currently just `_streamCts?.Cancel()`): every call site (Stop button, Esc from Phase 4, conversation switch) — the server cancel in Task 1 must fire ONLY when a generation is actually in flight.
4. Sidebar search today: how `ConversationListViewModel` filters (local title match? server `GET /conversations?q=`?), where the search text lives, and where a "message results" section can be added in `SidebarView.xaml`.
5. Turn completion ordering in `RunTurnAsync`'s finally/completion path: exactly where `IsStreaming` is set false relative to `ExtractPlanBlocks` (needed for Task 6).

---

## TASK 1 — Real stop: server-side cancel for chat-edge turns

**Server (verified):** `POST /api/v1/conversations/{id}/cancel` — no body, JWT auth, ownership-checked, idempotent; `200 {"cancelled": true|false}`; 404 when not owner.

1. Add `IChatEdgeApiClient.CancelStreamAsync(string conversationId, CancellationToken ct = default)` following the existing conventions (`Deadline()` 30s, error → `HttpRequestException`).
2. In `CancelStreaming`: when a turn is in flight (`IsStreaming` on the active assistant message / the same condition the Stop button uses) AND `_turnUsedChatEdge` is true AND the conversation has a server id, fire-and-forget the cancel call (log Debug on failure, log the returned `cancelled` flag). Local `_streamCts.Cancel()` behavior unchanged and still happens first.
3. Agent path needs NO server call: cancelling the local CTS closes the SSE connection to the gateway, which aborts generation — document this in a comment.
4. Guard: conversation-switch calls `CancelStreaming` too (R0.3) — the POST must not fire when nothing is streaming.

## TASK 2 — Surface model fallback (both paths)

**Server (verified):** the gateway emits fallback frames on BOTH paths. Chat-edge path: SSE event `fallback_info`, payload `{"requested_model","actual_model","reason","attempts":[...]}` — `ChatService.MapFrame` ALREADY produces `FallbackInfoEvent`, but `RunChatEdgeTurnAsync`'s switch (`ChatViewModel.cs:1048–1083`) has no case for it, so it's silently dropped. Agent path: the same info arrives INSIDE the OpenAI-compat stream as a frame shaped `{"type":"fallback_info","fallback_info":{...}}` — `GatewayChunkParser` currently ignores it (no `choices` property → empty result).

1. **Parser:** in `GatewayChunkParser.Parse`, before the `choices` check, handle a root object with `"type":"fallback_info"`: emit a new `AgentFallbackEvent(string RequestedModel, string ActualModel, string? Reason)` (add to `AgentStreamEvents.cs`). Unknown `type` values keep being ignored. Tests: the frame produces the event; a frame with type `fallback_info` but missing fields doesn't throw; other `type` frames still yield nothing.
2. **ChatViewModel:** handle `AgentFallbackEvent` (agent switch, ~line 944 area) and `FallbackInfoEvent` (chat-edge switch) identically: set a notice on the assistant message — reuse the `MetaLine`/`StatusText` surface (pick the one that persists after completion; `MetaLine` does) or prepend to it. Localized: `chat.modelFallback` — EN `"Answered by {0} — {1} was unavailable"` / TH `"ตอบโดย {0} — เนื่องจาก {1} ไม่พร้อมใช้งาน"` (format args: actual, requested). Must not overwrite the existing "model · N tokens" MetaLine — combine.
3. The notice must survive turn completion (`Complete()` builds MetaLine — check the ordering so it isn't clobbered).

## TASK 3 — Feedback to the server (chat-edge messages only)

**Server (verified):** `POST /api/v1/messages/{messageID}/feedback`, body `{"rating":"thumbs_up"|"thumbs_down"}`; upsert per (message, user); `200 {"feedback":{...}}`; 404 unknown id; 422 bad rating / non-assistant message.

1. Add `IChatEdgeApiClient.SendFeedbackAsync(string messageId, string rating, CancellationToken ct = default)`.
2. In the thumbs handlers (`ChatViewModel` `ThumbUp`/`ThumbDown`, ~line 1598 pre-Phase-4 numbering — locate them): keep the local optimistic toggle exactly as-is, then, ONLY when the message id is a server id (chat-edge-path message — use the R0.2 finding to derive a reliable check, e.g. conversation source or a flag on the VM set during chat-edge turns; do NOT ship a heuristic like "id parses as UUID"), fire-and-forget the POST. Failure → Debug log, local state stays (it's a preference signal, not critical data).
3. **Agent-path messages stay local-only** — the Phase 3 mirror keys messages by client_id and the import response returns no per-message server ids, so there is nothing to POST against. Document this in a comment; do NOT invent a server change.
4. Un-toggling (pressing the same thumb again): the server has no delete route (verified: only Create/upsert). Local untoggle stays local; when the user switches up→down, POST the new rating (upsert covers it). Comment this asymmetry.

## TASK 4 — "Chat is getting long" nudge (chat-edge conversations)

**Server (verified):** `GET /api/v1/conversations/{id}/context-info` → `200 {"conversation_id","total_budget","system_prompt_tokens","output_reserve","tool_reserve","messages_budget","messages_total","messages_would_send","compacted","extraction_triggered"}` (all ints + two bools; NO threshold/suggestion fields); 404 not owner; **503 when the service isn't wired** — treat 503/any failure as "no nudge".

1. Add `IChatEdgeApiClient.GetContextInfoAsync(...)` with a DTO matching the shape above.
2. New pure helper (tested): `ContextNudge.ShouldNudge(ContextInfoDto info)` → true when `info.Compacted || info.MessagesWouldSend < info.MessagesTotal`.
3. After a chat-edge turn completes (the `StreamCompletedEvent` case), fire-and-forget the GET; if `ShouldNudge` and this conversation hasn't nudged yet this session (a per-conversation HashSet in the VM), show an inline, dismissible notice near the composer or as a message-area banner — follow the existing inline-banner pattern from ChatView (R0-verify which pattern fits; no new dialog/toast systems). Localized: `chat.gettingLong` — EN `"This chat is getting long — older messages are now summarized before each reply. A new chat will be faster and more precise."` / TH `"แชทนี้เริ่มยาวแล้ว ระบบจะสรุปย่อข้อความเก่าก่อนตอบทุกครั้ง เปิดแชทใหม่จะเร็วและแม่นยำกว่า"`.
4. Agent path already has its own Phase 2 notices (`agent.contextTrimmed` / `agent.contextTooLong`) — do not double-nudge agent conversations.

## TASK 5 — Full-text search across message content (sidebar)

**Server (verified):** `GET /api/v1/search?q=...&scope=messages&limit=N` — SQL full-text (Postgres tsvector, `plainto_tsquery('simple')`), tenant+user scoped, limit capped at 20, empty `q` → 400. Response: `{"query","scope","results":[...],"total"}`; per-hit fields: `type` (`message`), `conversation_id`, `conversation_title`, `content` (truncated 500 runes), `role`, `date` (`YYYY-MM-DD`), `relevance` (`"high"|"medium"`). **There is NO `message_id` in the response** — a hit can only open the conversation, not jump to the message. Design for that; do not fake a jump.

**Coverage fact (verified):** the FTS index has no `origin`/`client_id` filter and the insert trigger indexes imported rows — so agent conversations mirrored by Phase 3 sync ARE searchable server-side. Unsynced local conversations (sync toggle off) are NOT; out of scope — do not build local FTS in this phase.

1. Add `IChatEdgeApiClient.SearchMessagesAsync(string query, int limit = 20, ...)` with DTOs matching the verified shape (`scope=messages` fixed).
2. Sidebar: when the search box text is ≥ 2 chars, debounce ~300 ms, call the endpoint, and render a **"In messages"** section BELOW the existing title-filtered results in `SidebarView.xaml`: each row = `conversation_title` (fallback: localized "Untitled") + one-line `content` snippet (secondary color, ellipsized) — follow the sidebar's existing row/ContextMenu styles, themed via DynamicResource. Loading state: reuse the sidebar's existing indeterminate progress pattern. Empty/error/offline → hide the section silently (Debug log).
3. **Hit → local conversation mapping** (pure helper, tested): a hit's `conversation_id` is a SERVER id. Resolve in order: (a) a local conversation whose `Id` equals it (server-source rows) → open it; (b) a local conversation whose `ServerId` equals it (Phase 3 mirror of an agent chat) → open the LOCAL conversation; (c) no match → ignore the hit (a conversation that exists server-side but was never pulled — opening it is a Phase-6 concern, don't half-build it).
4. Cancellation: a new keystroke cancels the in-flight search (CTS per query — don't render stale results over fresh ones).
5. Dict keys: `search.inMessages` EN `"In messages"` / TH `"ในข้อความ"` (+ any others you need, both languages).

## TASK 6 — Micro-fix: kill the last fence flash (≤66 ms at completion)

Phase 4 left one pre-existing seam: at completion, `IsStreaming=false` triggers the final full render BEFORE `ExtractPlanBlocks` strips fences via `SetDisplayContent`, so raw fence text can flash for one render cycle. Fix by ordering: in the turn-completion path (R0.5), call `ExtractPlanBlocks` (which sets `_displayContent`) BEFORE setting `IsStreaming = false`. Constraints: persistence must keep saving the RAW `Content` (fences included — that's the reload contract, `MessageViewModel.cs:51-55`), and the history-load path (`ExtractPlanBlocks` at `ChatViewModel.cs:577`) is already fine. Verify `ExtractPlanBlocks`'s guard conditions still hold when called pre-completion (it checks `message.IsError`/empty/already-extracted).

---

## Final verification checklist

1. `dotnet build` 0/0; `dotnet test` all green including new parser/nudge/mapping tests and `DictParityTests`.
2. Answer all R0 questions explicitly.
3. Fire-and-forget audit: list every new network call site and confirm none can block, throw into, or delay a turn or UI handler (all wrapped, all logged).
4. Path-discipline audit: state for each task which path(s) it affects (cancel: chat-edge only; fallback: both; feedback: chat-edge only; nudge: chat-edge only; search: server data incl. mirrors) and confirm no agent-path regression (Phases 1–4 tests all still green).
5. Summarize changes file by file + note anything you deliberately deferred.
