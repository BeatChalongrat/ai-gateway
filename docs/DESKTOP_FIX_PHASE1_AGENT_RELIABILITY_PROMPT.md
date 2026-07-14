# DESKTOP FIX — PHASE 1: Agent Reliability

You are working on **SranDesktop**, a Windows WPF (.NET 8) desktop client located at `SranDesktop/` in this repo. It has a client-driven agent loop (`ChatOrchestrator`) that streams OpenAI-format completions from the SRAN gateway (`/v1/chat/completions`, auth = self-provisioned `gw_` API key) and executes tools locally.

This phase fixes **three reliability bugs** that make the agent stall, die permanently, or hang. Do them in order. Do NOT start Phase 2 work (context management) — that is a separate prompt.

## Ground rules

- Read every file you touch IN FULL before editing.
- Follow the existing code style exactly (file-scoped namespaces, sealed classes, existing logging patterns via `ILogger<T>`).
- Do not change public interface signatures in `SranDesktop.Application/Interfaces/` unless a task explicitly says so.
- Do not weaken the DLP handling (`IsDlpBlocked`) or the permission/consent flow in `ChatOrchestrator`.
- Every fix must come with unit tests in `SranDesktop.Tests` following the existing xunit + Moq + FluentAssertions patterns (see `SranDesktop.Tests/Agent/ChatOrchestratorTests.cs` and `GatewayChunkParserTests.cs` for reference).
- After all changes: `dotnet build SranDesktop/SranDesktop.sln` must succeed and `dotnet test` must pass — all existing tests AND your new ones. Do not delete or weaken existing tests.
- Update the stale numbers in `SranDesktop/docs/ARCHITECTURE.md` ONLY if your change makes them wrong; otherwise leave docs alone.

---

## TASK 1 — Fix dropped tool calls when the stream ends without `finish_reason`

### Files
- `SranDesktop/src/SranDesktop.Application/Models/GatewayChunkParser.cs`
- `SranDesktop/src/SranDesktop.Application/Services/ChatOrchestrator.cs`
- Tests: `SranDesktop/src/SranDesktop.Tests/Agent/GatewayChunkParserTests.cs`, `ChatOrchestratorTests.cs`

### The bug (verified)
`GatewayChunkParser.Parse` only drains accumulated tool calls when a chunk carries `finish_reason == "tool_calls"` (or `"stop"` with calls accumulated) — see lines 74–88. If a provider ends the stream with a bare `data: [DONE]` (line 20–21 returns only `AgentStreamDoneEvent`) or the connection simply closes, any tool calls sitting in `_toolCalls` are silently dropped. In `ChatOrchestrator.RunAsync`, `pendingToolCalls` then stays empty, so line 114–119 treats the turn as a final answer and the loop exits — the agent "finishes" mid-task.

### The fix
1. Add a public method to `GatewayChunkParser`:
   ```csharp
   /// <summary>Drains any tool calls still accumulated (stream ended without a
   /// finish_reason). Returns an empty list when nothing is pending.</summary>
   public IReadOnlyList<GatewayToolCall> DrainRemaining()
   ```
   It must reuse the existing private `DrainToolCalls()` logic (ordering by index, skipping accumulators with null `Name`, defaulting empty args to `"{}"`).
2. In `GatewayChunkParser.Parse`, when the payload is `[DONE]` **and** `_toolCalls` contains at least one accumulator with a non-null `Name`, emit `new AgentToolCallsEvent(DrainToolCalls())` **before** the `AgentStreamDoneEvent`.
3. In `ChatOrchestrator.RunAsync`, after the `await foreach` over `_gateway.StreamCompletionAsync(...)` completes (i.e., the stream ended — covers the connection-closed-without-DONE case), if `!sawError`, call `parser.DrainRemaining()` and add any returned calls to `pendingToolCalls` (guard against double-adding: only add calls whose `Id` is not already in `pendingToolCalls`).
4. While you are there, remove the dead `_ = done;` at line 140 and the now-unused `done` variable if nothing else needs it (check first — do not break the `AgentStreamDoneEvent` case handling for content-only turns).

### Acceptance tests (add these)
- Parser: feed a chunk with a complete tool call delta (id/name/arguments) but NO finish_reason, then feed `[DONE]` → result must contain an `AgentToolCallsEvent` with that call, followed by `AgentStreamDoneEvent`.
- Parser: feed `[DONE]` with nothing accumulated → only `AgentStreamDoneEvent` (no empty `AgentToolCallsEvent`).
- Parser: accumulator with arguments but no name is still skipped on `[DONE]`.
- Orchestrator: mock a gateway stream that yields a tool-call delta chunk and then ends (no finish_reason frame, no `[DONE]`) → the tool must still be resolved and executed, and the loop must continue to a second iteration.

---

## TASK 2 — Auto re-provision the gateway agent key on 401 (revoked/rotated key)

### Files
- `SranDesktop/src/SranDesktop.Infrastructure/Api/GatewayApiClient.cs`
- `SranDesktop/src/SranDesktop.Infrastructure/Api/AgentKeyProvider.cs`
- `SranDesktop/src/SranDesktop.Application/Interfaces/` — `IAgentKeyProvider` (you MAY extend this interface, see below)
- Tests: add `SranDesktop/src/SranDesktop.Tests/Agent/GatewayApiClientAuthTests.cs` (or extend an existing suitable file)

### The bug (verified)
`AgentKeyProvider.GetOrProvisionKeyAsync` returns the DPAPI-stored key forever (lines 47–49). `AgentKeyProvider.Invalidate()` (lines 65–69) is **never called anywhere in the codebase**. When the gateway rejects a revoked/rotated key, the backend returns **HTTP 401** with body:
```json
{"error":{"code":"authentication_error","message":"Invalid, expired, or revoked API key"},"request_id":"..."}
```
(verified in `backend/app/api/deps.py:242-243` + `backend/app/middleware/error_handler.py:42`). `GatewayApiClient.StreamCompletionAsync` just yields that body as an error frame (lines 75–84), so Agent Mode is permanently wedged until the user manually clears credentials.

### The fix
1. Add to `IAgentKeyProvider` and `AgentKeyProvider`:
   ```csharp
   /// <summary>Drops the cached/stored key and provisions a fresh one.
   /// Returns null when provisioning fails.</summary>
   Task<string?> ReprovisionAsync(CancellationToken ct = default);
   ```
   Implementation: take `_provisionLock`, clear `_cached`, delete the stored credential (reuse `Invalidate()` internals), then run the existing `ProvisionAsync` path and save/cache on success. Must be safe under concurrent callers (single-flight, same as `GetOrProvisionKeyAsync`).
2. In `GatewayApiClient.StreamCompletionAsync`, when the response status is **401** (only 401 — do not retry other statuses here):
   - Log a warning ("agent key rejected, re-provisioning").
   - Dispose the response, call `await _keyProvider.ReprovisionAsync(ct)`.
   - If a new key is returned, rebuild the `HttpRequestMessage` (a request message cannot be reused after sending — construct a fresh one with the same JSON content) and send **once** more. If the retry also fails with 401, yield the error body as today. Never loop more than one retry.
   - If re-provisioning returns null, yield a clear error frame: code `agent_key_reprovision_failed`, message telling the user to check they are signed in.
   - Careful: this method is an async iterator — `yield` is not allowed inside `try/catch` blocks that catch. Follow the existing pattern in this file (capture the error string in a local, yield after the catch).
3. Apply the same 401 → reprovision-once handling to `SearchAsync` (it throws `HttpRequestException` today; on first 401, reprovision and retry once before throwing).

### Acceptance tests (add these)
Use a stub `HttpMessageHandler` (see existing test patterns) so no real network is used.
- First request returns 401 with the `authentication_error` body, key provider's `ReprovisionAsync` returns a new key, second request returns a normal SSE stream → the caller receives the streamed frames and `ReprovisionAsync` was called exactly once.
- Both requests return 401 → the 401 error body is yielded as a frame, and `ReprovisionAsync` was called exactly once (no infinite loop).
- Non-401 error (e.g. 502) → `ReprovisionAsync` is NOT called; body yielded as today.
- `ReprovisionAsync` returns null → the `agent_key_reprovision_failed` frame is yielded.
- `AgentKeyProvider.ReprovisionAsync`: concurrent double-call provisions only once (single-flight).

---

## TASK 3 — Stream idle watchdog + one-shot retry + Retry-After on 429

### Files
- `SranDesktop/src/SranDesktop.Infrastructure/Api/GatewayApiClient.cs`
- Tests: extend the file from Task 2 or add `GatewayApiClientResilienceTests.cs`

### The bugs (verified)
- The `HttpClient` used by `GatewayApiClient` has an **infinite timeout** (`AppHostBuilder.cs:98`) and `StreamCompletionAsync` imposes **no per-call or idle deadline**. `ChatOrchestrator`'s 10-minute cap is only checked **between** iterations, so a single stalled stream (server keeps the socket open, sends nothing or only SSE keep-alive comments) hangs the turn indefinitely with no way out except the user's Stop button.
- A transient connection failure before the first token kills the turn with no retry (`RetryHelper` exists at `SranDesktop.Infrastructure/Api/RetryHelper.cs` but is used only for chat-edge GETs).
- 429 responses are surfaced as raw errors; the `Retry-After` header is ignored.

### The fix
1. **Idle watchdog**: wrap the SSE read loop so that if **no frame arrives for 90 seconds**, the stream is aborted and an error frame is yielded (`code: "stream_stalled"`, message telling the user the connection stalled and to retry). Implementation approach: create a linked `CancellationTokenSource` from the caller's token; call `cts.CancelAfter(TimeSpan.FromSeconds(90))` before the loop and **reset it (`CancelAfter(90s)` again) on every frame received**. Distinguish watchdog cancellation from user cancellation: if the linked source fired but the caller's `ct` is not cancelled, it was the watchdog → yield the `stream_stalled` error frame instead of throwing. Make the timeout a `private const int IdleTimeoutSeconds = 90;` so tests can't accidentally depend on magic numbers scattered around.
   - Note: SSE keep-alive comment lines (`:` prefixed) are swallowed inside `SseStreamReader.ParseAsync` and never surface as frames (verified, `SseStreamReader.cs:44-45`). That is acceptable for this task: a stream sending only keep-alives for 90s with zero real frames SHOULD be treated as stalled. Do not modify `SseStreamReader`.
2. **Connection retry (pre-first-frame only)**: if the initial `SendAsync` throws `HttpRequestException`/`IOException`, or returns **429/5xx**, retry the whole request **once** after a delay, but ONLY if no frame has been yielded yet (never retry mid-stream — partial output must not be duplicated). Delay: for 429, parse the `Retry-After` header (both delta-seconds and HTTP-date forms; cap at 30s; fall back to 2s when absent/unparseable). For connection errors/5xx, use 2s. Honor cancellation during the delay (`Task.Delay(delay, ct)`).
3. Keep the existing behavior for the 401 path from Task 2 (auth retry and transient retry are separate; a request may use each at most once).
4. Do NOT add retries to the request once streaming has begun, and do NOT change `ChatEdgeApiClient` in this phase.

### Acceptance tests (add these)
- A stubbed stream that yields one frame then never completes: with the watchdog timeout shortened via reflection-free means (make the constant `internal` and use `[assembly: InternalsVisibleTo]` if needed — the test project already has visibility patterns; check how other internals are tested) → the caller receives the `stream_stalled` error frame instead of hanging. If making the timeout injectable via an optional constructor parameter is cleaner, do that instead of `InternalsVisibleTo`.
- First `SendAsync` throws `HttpRequestException`, second succeeds → frames are delivered; exactly 2 sends.
- First response is 429 with `Retry-After: 1`, second succeeds → frames delivered; the delay respected (assert ≥1s elapsed or verify via injected delay abstraction — prefer injecting a delay func for testability over real sleeps).
- 429 on both attempts → the 429 body is yielded as an error frame after exactly 2 sends.
- User cancellation during the retry delay → `OperationCanceledException` propagates (not swallowed).

---

## Final verification checklist

1. `dotnet build SranDesktop/SranDesktop.sln` — zero errors, zero new warnings.
2. `dotnet test` — all existing tests pass (especially `Agent/ChatOrchestratorTests`, `Agent/GatewayChunkParserTests`) plus all new tests.
3. Grep check: `Invalidate()` is either used by `ReprovisionAsync` internally or removed from the interface if fully superseded — no dead public API left behind.
4. Manual trace review: read your final `StreamCompletionAsync` top-to-bottom and confirm (a) at most ONE transient retry and ONE auth retry per call, (b) no `yield` inside a catch block, (c) response objects disposed on all paths.
5. Summarize what changed, file by file, and list any follow-up risks you noticed for Phase 2 (context management) WITHOUT implementing them.
