# DESKTOP FIX — PHASE 2: Context Window Management

You are working on **SranDesktop**, a Windows WPF (.NET 8) desktop client at `SranDesktop/` in this repo, plus a small change in the **Python gateway backend** at `backend/`. Phase 1 (agent reliability: tool-call drain, 401 re-provision, stream watchdog) is already merged — do not re-do or regress it.

## The problem this phase solves (verified in code)

Agent Mode has **zero context management**:

1. `ChatViewModel.BuildGatewayMessages` (`SranDesktop/src/SranDesktop.Presentation/ViewModels/ChatViewModel.cs`, ~line 1297) appends **every** message in the conversation to every request, unbounded.
2. Within a turn, `ChatOrchestrator.RunAsync` (`SranDesktop/src/SranDesktop.Application/Services/ChatOrchestrator.cs`) appends assistant tool-call messages, tool results (up to 50K chars each via `ToolResult.TruncatedContent`), and **full-resolution screenshots as base64 data-URI vision messages** (lines ~246–257) to the same list — and re-sends ALL of it on every one of up to 25 iterations. A long computer-use session re-uploads every old screenshot every step.
3. `GatewayCompletionRequest` (`SranDesktop/src/SranDesktop.Application/Models/GatewayModels.cs`) has **no `max_tokens`** field (the gateway accepts it: `backend/app/schemas/public.py:196-210`, `max_tokens: Optional[int] = Field(None, ge=1, le=128000)`).
4. When the context window is exceeded, the provider returns 400 and the gateway maps it to a **generic 502** `upstream_bad_response` (`backend/app/services/providers/error_handler.py:92-96` → `backend/app/middleware/error_handler.py:57`), so the client cannot distinguish "context too long" from any other upstream failure. The provider's real error is buried in `error.details.body`.
5. `GatewayChunkParser` (`SranDesktop/src/SranDesktop.Application/Models/GatewayChunkParser.cs`, error branch ~lines 45–53) extracts only `error.message` — the error **code** never reaches the orchestrator.

This phase = **PART A (gateway, Python)**: typed `context_length_exceeded` error. **PART B (desktop, C#)**: token budgeting, history trimming, screenshot pruning, `max_tokens`, and auto-trim-and-retry on context errors. Do PART A first; PART B must also work against an **old gateway** that hasn't deployed PART A (fallback detection included below).

## Ground rules

- Read every file you touch IN FULL before editing. Several integration points below say "verify first" — actually verify, do not assume.
- Follow existing code style in each codebase (C#: file-scoped namespaces, sealed classes, `ILogger<T>`; Python: existing exception/middleware patterns).
- Desktop: every new behavior needs unit tests in `SranDesktop.Tests` (xunit + Moq + FluentAssertions, follow `Agent/` folder patterns). Gateway: pytest tests following existing patterns in `backend/tests/`.
- Do NOT touch: the permission/consent flow, DLP handling (`IsDlpBlocked`), Phase 1's retry/watchdog logic in `GatewayApiClient` (you will not need to modify that file), `ChatEdgeApiClient`, or the chat-edge Go service.
- Localization: any new user-facing string must be added to BOTH `En` and `Th` dictionaries in `SranDesktop/src/SranDesktop.Presentation/Localization/Dict.cs` — `DictParityTests` enforces identical key sets and will fail otherwise. Write natural Thai, not machine-translated Thai.
- Finish state: `dotnet build SranDesktop/SranDesktop.sln` zero errors/zero new warnings; `dotnet test` all green (existing + new). For the gateway: run at least the test files you added/modified with pytest.

---

# PART A — Gateway: typed `context_length_exceeded` error (Python)

### Files
- `backend/app/services/providers/error_handler.py`
- `backend/app/core/exceptions.py`
- `backend/app/middleware/error_handler.py`
- Tests: add/extend under `backend/tests/` (find where provider error-handling tests live first; follow that pattern)

### A1 — New exception type
In `backend/app/core/exceptions.py`, add (following the existing exception class conventions in that file, e.g. `UpstreamBadResponseError` at ~line 170):

- `ContextLengthExceededError` with code `context_length_exceeded`, carrying `provider` and the provider's original message in `details`.

### A2 — Detection in the provider error handler
In `backend/app/services/providers/error_handler.py`, at the point where a provider response with `status >= 400` (excluding 401/403/404/429) is currently mapped to `UpstreamBadResponseError` (~lines 92–96): **before** raising the generic error, inspect the provider response body (status 400 only) for context-length signatures, case-insensitively:

- OpenAI-compatible: error `code == "context_length_exceeded"`, or message containing `"maximum context length"` or `"context window"` together with `"token"`.
- Anthropic: message containing `"prompt is too long"`.
- Google/Gemini: message containing `"exceeds the maximum number of tokens"` or `"input token count"` + `"exceeds"`.

Put the patterns in a module-level tuple/constant so they are testable and extendable. If matched → raise `ContextLengthExceededError` (include provider name + the original provider message in details). Otherwise behavior is unchanged.

**Do NOT make this error fallback-eligible** — do not touch the routing/fallback logic. Retrying a too-long prompt on another model wastes money; the client is responsible for trimming.

### A3 — HTTP mapping
In `backend/app/middleware/error_handler.py`, map `ContextLengthExceededError` → **HTTP 400** with the standard envelope produced by `_build_body` (~lines 75–91):

```json
{"error":{"code":"context_length_exceeded",
          "message":"The request exceeds the model's context window. Shorten the conversation and try again.",
          "details":{"provider":"...", "provider_message":"..."}},
 "request_id":"..."}
```

### A4 — Verify BOTH request modes
`/v1/chat/completions` is used in streaming and non-streaming mode. Read `backend/app/api/routes/public_chat.py` enough to confirm how a pre-stream provider error is surfaced when `stream: true` (error envelope frame vs HTTP status), and make sure the typed code reaches a streaming client in whichever form that path uses. State in your summary exactly what a streaming client receives.

### A5 — Tests (pytest)
- Provider 400 body with OpenAI `context_length_exceeded` code → `ContextLengthExceededError` raised.
- Provider 400 with Anthropic `"prompt is too long"` message → same.
- Provider 400 with an unrelated error body → still `UpstreamBadResponseError` (no behavior change).
- Provider 500 with a body that happens to contain "context window" → NOT matched (status-400-only rule).
- Middleware maps the new exception to HTTP 400 with code `context_length_exceeded`.

---

# PART B — Desktop: budgeting, trimming, pruning, retry (C#)

## B0 — MANDATORY reconnaissance before writing any code

Read fully and note answers in your final summary:

1. `ChatViewModel.BuildGatewayMessages` (~line 1297) and `BuildSystemExtrasAsync` (~line 1011): what exactly goes into history each turn? **Critically: do tool results / screenshot vision messages from PREVIOUS turns get rebuilt into the next turn's history, or does only the plain user/assistant text survive across turns?** Your trimming design below must match reality (if screenshots don't cross turns, cross-turn pruning is a no-op and within-turn pruning is the whole game).
2. `GatewayModels.cs`: how are null properties serialized (check for `JsonIgnoreCondition` attributes / the `JsonOptions` used) — your new `MaxTokens` must follow the same convention.
3. `SranDesktop/src/SranDesktop.Application/Models/ChatEdgeModels.cs` + `ChatEdgeApiClient.ListModelsAsync`: does the model DTO already deserialize `context_window` from `GET /api/v1/models`? (The server sends it — verified in chat-edge `internal/handler/model.go`.) If the field is missing from the DTO, add it.
4. `AgentStreamEvents.cs`: current shape of `AgentErrorEvent` and how tests construct it.

## B1 — Token estimator (new file)

`SranDesktop/src/SranDesktop.Application/Services/TokenEstimator.cs` — a `public static class`, pure functions, no dependencies:

- `EstimateMessage(GatewayMessage m)` and `EstimateMessages(IEnumerable<GatewayMessage>)`.
- Rules: string content → `ceil(chars / 4)`. Multimodal `object[]` content → text blocks `chars/4`; each `image_url` block → flat **1100** tokens (`private const int ImageTokenEstimate = 1100`). Tool calls → estimate over serialized function name + arguments. Add a flat **4-token** per-message overhead. Tool-role messages → content `chars/4`.
- This is a deliberate over-under approximation; document that in the XML doc comment. Do not add a tokenizer dependency.

## B2 — Screenshot pruner (new file)

`SranDesktop/src/SranDesktop.Application/Services/ScreenshotPruner.cs`:

- `public static bool Prune(List<GatewayMessage> messages, int keepLatest = 2)` — mutates in place, returns whether anything was pruned.
- A "screenshot message" = role `user`, content is the multimodal `object[]` produced by `ChatOrchestrator` (text block starting with `"[Screenshot from"` + `image_url` block). Detect it structurally + by that marker; you may extract the message-construction in `ChatOrchestrator` into a small factory (e.g. `ScreenshotMessage.Create(toolName, dataUri)`) so detection and construction share one definition instead of string-matching two places. Keep it in the Application layer.
- All screenshot messages except the **latest `keepLatest`** get their content replaced with the plain string: `"[An earlier screenshot was removed to save context space. Take a new screenshot if you need to see the current state.]"` — same role, message NOT removed (removing it would confuse the model's narrative of what it did).
- Must be idempotent (already-pruned messages are plain strings and no longer match the detector).

## B3 — Context budgeter (new file)

`SranDesktop/src/SranDesktop.Application/Services/ContextBudgeter.cs`:

```csharp
public sealed record BudgetResult(bool Trimmed, int OmittedMessages, int EstimatedTokens);
public static BudgetResult FitToBudget(List<GatewayMessage> messages, int contextWindow, int outputReserve = 8192)
```

- Budget = `(contextWindow - outputReserve) * 0.9` (10% safety margin for estimator error). Constants named, not inline.
- If `EstimateMessages` ≤ budget → return unchanged.
- Trimming rules (this is the part where correctness matters — providers hard-reject malformed histories):
  - **Never remove**: leading `system` message(s); the **final group** (see grouping below); the trim-note itself.
  - **Grouping**: walk the list after the system prologue and split into groups, where a new group starts at each `user` message whose `Content` is a plain string (a real user turn). Everything following it — assistant messages (with or without tool_calls), `tool` messages, multimodal screenshot user-messages — belongs to that group until the next plain-string user message. This guarantees a `tool` message is never separated from the `assistant` message carrying its `tool_call_id` (providers reject orphaned tool results).
  - Remove whole groups, **oldest first**, until under budget or only the final group remains.
  - After trimming, insert ONE note message at the trim point (right after the system prologue), role `user`, content: `"[Note: {N} earlier messages in this conversation were omitted because the conversation exceeded the model's context window. Ask the user if you are missing needed context.]"`.
  - If even `system + note + final group` exceeds budget (a monster final turn), run `ScreenshotPruner.Prune(messages, keepLatest: 1)` and re-estimate; if still over, return the result with `Trimmed = true` anyway (the request will be sent and may fail — the retry path in B6 handles it). Never drop the final group.

## B4 — `max_tokens` on the request

- Add `MaxTokens` (`[JsonPropertyName("max_tokens")]`, `int?`) to `GatewayCompletionRequest` in `GatewayModels.cs`, following the file's existing null-serialization convention (B0.2).
- `ChatOrchestrator` sets it to a named constant `DefaultMaxOutputTokens = 8192` on every request. Keep it a constant for now (a settings surface is out of scope).

## B5 — Wire pruning + budgeting into the loop

In `ChatOrchestrator.RunAsync`, at the top of every iteration **before** building `GatewayCompletionRequest`:

1. `ScreenshotPruner.Prune(messages, keepLatest: 2)`
2. `ContextBudgeter.FitToBudget(messages, contextWindow, ...)`
3. If the budgeter trimmed, yield a new event `AgentContextTrimmedEvent(int OmittedMessages)` (add to `AgentStreamEvents.cs`) so the UI can inform the user.

Getting `contextWindow` into the orchestrator: extend `IChatOrchestrator.RunAsync` with an additional parameter (e.g. `int? contextWindow = null` or a small options record — pick whichever fits the interface style; updating the interface IS allowed in this phase). `ChatViewModel` resolves it from the loaded model list (`AvailableModels` — the model DTO's `context_window`, see B0.3); when unknown (model list failed to load / field absent) pass null and the orchestrator falls back to `private const int FallbackContextWindow = 100_000`.

Also (small, flagged in Phase 1 follow-ups): the `MaxDuration` deadline is currently only checked in the `while` condition between iterations. Add a deadline check **before each tool execution** in the tool-call loop; when exceeded, stop gracefully with the same time-limit error message instead of starting another tool.

## B6 — Context-error detection + one automatic trim-and-retry

1. **Parser**: extend `AgentErrorEvent` with `string? Code = null` (keep source compatibility with existing constructions/tests — verify B0.4). In `GatewayChunkParser`'s error branch, extract `error.code` when present. Additionally — fallback for an old gateway without PART A — when `code == "upstream_bad_response"`, serialize `error.details` (if present) and scan it for the same signatures as PART A (`"context_length_exceeded"`, `"maximum context length"`, `"prompt is too long"`, `"exceeds the maximum number of tokens"`, case-insensitive); on match, normalize the event's `Code` to `"context_length_exceeded"`. Put the signature list in one internal static readonly array shared with tests.
2. **Orchestrator**: in `RunAsync`, when an `AgentErrorEvent` with `Code == "context_length_exceeded"` arrives and this turn has not yet used its context-retry (one boolean flag per `RunAsync` call, NOT per iteration):
   - Do NOT `yield return` the error to the UI yet.
   - Aggressive recovery: `ScreenshotPruner.Prune(messages, keepLatest: 1)`, then `ContextBudgeter.FitToBudget` with `contextWindow / 2` (half budget — the estimator clearly undercounted for this provider).
   - Yield `AgentContextTrimmedEvent` and retry the SAME iteration (do not increment the iteration count for the retry; simplest correct approach: `iteration--` before `continue`, or restructure so the retry re-enters the request phase — your choice, but MaxIterations semantics must not lose a step to the retry).
   - If the retry ALSO fails with a context error → yield the error event with a clearer message appended: suggest starting a new conversation. Then stop the turn as today.
   - Any other `Code` (or null) → exactly today's behavior (yield error, stop). No new retry paths for non-context errors — Phase 1's transport retries already cover transient failures at the HTTP layer.

## B7 — UI notice

In `ChatViewModel` (agent path, `RunTurnAsync`'s event loop): handle `AgentContextTrimmedEvent` by showing a non-blocking inline notice on the current assistant message or status area (follow how `AgentStatusEvent` is surfaced today — read it first). Localized strings (both languages, Dict.cs):

- `agent.contextTrimmed` — EN: `"Older messages were trimmed to fit the model's context window."` / TH: `"ตัดข้อความเก่าบางส่วนออกเพื่อให้พอดีกับหน้าต่างบริบทของโมเดล"`
- `agent.contextTooLong` — EN: `"This conversation is too long for the model. Start a new chat for best results."` / TH: `"บทสนทนานี้ยาวเกินขีดจำกัดของโมเดลแล้ว แนะนำให้เริ่มแชทใหม่"`

No new dialogs, no toasts (the app has no toast system) — inline/status only.

## B8 — Desktop tests (new + extended)

`TokenEstimatorTests`: string content; multimodal with 2 images ≈ text + 2200 + overhead; tool-call args counted; empty message → overhead only.

`ScreenshotPrunerTests`: 4 screenshot messages, keep 2 → oldest 2 replaced with placeholder string, roles preserved, order preserved, returns true; idempotent second call returns false; non-screenshot multimodal user message (no `"[Screenshot from"` marker) untouched.

`ContextBudgeterTests`:
- Under budget → untouched, `Trimmed == false`.
- Over budget → oldest groups removed, system message intact, final group intact, exactly one note inserted, `OmittedMessages` correct.
- A group containing assistant-with-tool_calls + its tool results is removed **atomically** (construct a history where naive per-message trimming would orphan a tool message; assert no `tool` message survives whose preceding assistant tool_call is gone).
- Monster final group → screenshot-pruned to 1, never dropped.

`GatewayChunkParserTests` (extend): error envelope with `code` → `AgentErrorEvent.Code` populated; `upstream_bad_response` + context signature in `details` → Code normalized to `context_length_exceeded`; `upstream_bad_response` + unrelated details → Code stays `upstream_bad_response`.

`ChatOrchestratorTests` (extend): context error on iteration 1 → prune+trim happen, request re-sent, second attempt succeeds → turn completes normally and exactly one `AgentContextTrimmedEvent` was yielded before the retry; context error twice → error surfaced with the new-chat suggestion, no infinite loop; non-context error → no retry (existing behavior test still green); every iteration's request now carries `MaxTokens = 8192`.

---

## Final verification checklist

1. Gateway: `pytest` on the files you added/modified — green. Desktop: `dotnet build` 0/0, `dotnet test` all green including `DictParityTests`.
2. Trace review of `ChatOrchestrator.RunAsync` top-to-bottom: (a) prune → budget → request order on every iteration, (b) context-retry at most once per turn and does not consume an iteration, (c) no `yield` inside catch blocks, (d) Phase 1 behaviors (DrainRemaining, dedup, watchdog error frames) untouched.
3. Construct-by-hand sanity check: write out (in your summary, not code) the message list of a 3-turn conversation with 5 screenshots after pruning+trimming — show which messages survive. This catches grouping mistakes cheaply.
4. Summarize what changed file by file, answer the B0 reconnaissance questions explicitly, and list follow-up risks for Phase 3 (data safety: SQLite migrations, atomic writes, agent-conversation sync) WITHOUT implementing them.
