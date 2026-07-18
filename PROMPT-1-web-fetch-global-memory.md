# Task: Add `web_fetch` tool + Global Memory to SranDesktop

Work in `SranDesktop/` (WPF .NET 8, clean architecture: App → Presentation → Application → Infrastructure → Domain). Read `SranDesktop/docs/ARCHITECTURE.md` first. Build with `dotnet build SranDesktop/SranDesktop.sln`, test with `dotnet test`.

Two features, independent — implement both, in order.

---

## Feature 1: `web_fetch` tool

Today the agent has `web_search` (gateway snippets only) and `open_url` (just opens the user's browser). It CANNOT read web page content. Add a `web_fetch` tool so research tasks work like Claude's WebFetch.

### Implementation

**New file:** `src/SranDesktop.Infrastructure/Mcp/Tools/WebFetchTool.cs`

Follow the existing tool pattern exactly (see `WebSearchTool.cs` and `OpenUrlTool.cs` for reference):
- Implement `IMcpTool`: `Name = "web_fetch"`, `Category = ToolCategory.Network`, `DefaultPermission = PermissionLevel.AllowAlways` (read-only, like `web_search`).
- Description (model-facing): "Fetch a web page and return its readable text content. Use after web_search to read the actual pages, or when the user gives you a URL. Returns title + main text with scripts/styles/navigation removed."
- Params schema: `url` (string, required, http/https only), `max_length` (integer, optional, default 50000, clamp 1000–200000).

Behavior:
- Use `IHttpClientFactory` (Infrastructure already references `Microsoft.Extensions.Http`; register a named client if needed in `AppHostBuilder.cs`). Timeout 30s. Follow redirects (max 5). Send a normal browser-ish User-Agent and `Accept: text/html,*/*`.
- **SSRF guard:** resolve the host and refuse private/loopback/link-local ranges (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, ::1, fc00::/7) and non-http(s) schemes. Also re-check after redirects.
- Cap download at 5 MB (abort with a clear error beyond that).
- Content handling by Content-Type:
  - `text/html` → extract readable text: strip `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, comments; take `<title>`; convert block elements to newlines, `<a href>` to `text (url)` only when the href differs from the text; decode HTML entities; collapse ≥3 newlines to 2. Do this with regex/string processing — **do NOT add new NuGet packages.**
  - `text/plain`, `application/json`, `text/*` → return as-is.
  - Anything else (images, pdf, binary) → return an error telling the model the content type is unsupported.
- Result text: `"# {title}\n\n{text}"`, truncated to `max_length` with a trailing note `"[truncated — {n} of {total} chars]"` when cut. Use `ToolResult.Ok(...)` / `ToolResult.Fail(...)`.

**Registration:** add to `McpToolRegistry` in `src/SranDesktop.App/Hosting/AppHostBuilder.cs` next to the other tools. Unlike `web_search`, `web_fetch` is ALWAYS listed (not gated by the composer web-search toggle / `WebSearchState`).

**Danger classification:** add `"web_fetch"` to the `Safe` set in `src/SranDesktop.Application/Services/ToolDangerClassifier.cs` (read-only, no side effects) so the "auto" approval mode doesn't pause on it.

**Tests:** in `src/SranDesktop.Tests`, add `WebFetchToolTests.cs` covering the HTML→text extraction (title, script/style stripping, entity decoding, truncation) and the SSRF/scheme rejection logic. Structure the extraction + URL-validation logic as `internal static` methods so they're testable without network. Follow the existing xunit + FluentAssertions style.

---

## Feature 2: Global memory (cross-conversation `remember`)

Today `remember` notes are conversation-scoped only (SQLite via `SqliteConversationStore.AddNoteAsync`, rendered into a never-trimmed `## Notes` system block by `AgentNotesFormatter`). Add a **global scope** so durable user facts survive across conversations, like Claude's memory.

### Implementation

1. **Storage** — `src/SranDesktop.Infrastructure/Storage/SqliteConversationStore.cs`:
   - The notes table gains a scope. Follow the store's existing lightweight-migration pattern (look at how earlier columns/tables were added in the schema-setup section). Either add a nullable `scope TEXT DEFAULT 'conversation'` column, or store global notes under a reserved conversation id `"__global__"` — pick whichever fits the existing schema with the smallest migration, and keep backward compatibility with existing rows.
   - Add `GetGlobalNotesAsync(ct)` and allow `AddNoteAsync` to target global scope. Cap global notes at the newest 50 (delete oldest beyond that, same style as any existing caps).

2. **Tool** — `src/SranDesktop.Infrastructure/Mcp/Tools/RememberTool.cs`:
   - Add optional `scope` param: enum `"conversation"` (default) | `"global"`.
   - Update the description so the model chooses correctly: conversation scope for task-specific facts (build command of this repo, file locations); global scope for durable user-level facts (user's name/team, preferences like "ชอบรายงานแบบสั้น", recurring workflows). One sentence per note, never secrets/credentials.

3. **Prompt injection** — `src/SranDesktop.Application/Services/AgentNotesFormatter.cs` (and its call site in `ChatViewModel.BuildGatewayMessages` / wherever the `## Notes` block is assembled):
   - Render two sections: `## Memory (global)` (global notes, newest-first) then `## Notes (this conversation)`. Keep the existing total char cap behavior; give global notes their own 2000-char cap so a busy conversation can't evict them. The block stays in the never-trimmed system prologue.

4. **UI** — `MemoryView` / `MemoryViewModel` (Presentation): show global notes in their own section with per-note delete (delete = remove row). Add the few needed strings to `src/SranDesktop.Presentation/Localization/Dict.cs` in **both** the `En` and `Th` dictionaries (e.g. `memory.global` = "Global memory" / "ความจำส่วนกลาง", `memory.conversation` = "This conversation" / "บทสนทนานี้"). Follow existing `loc:T` usage.

5. **Tests:** extend the existing store/notes tests for scope round-trip, the 50-note cap, and formatter section ordering.

---

## Conventions (important — match the codebase)

- MVVM via CommunityToolkit (`[ObservableProperty]`, `[RelayCommand]`); services registered as singletons in `AppHostBuilder.cs`; interfaces live in `SranDesktop.Application/Interfaces`.
- Comment style: concise `///` XML summaries explaining WHY, often tagged with a phase/feature marker — mimic it.
- No new NuGet dependencies for either feature.
- Localization: every user-visible string goes through `Dict.cs` (En + Th both, keys must match exactly).
- Don't touch: `PathGuard`, permission/consent flow, `ChatOrchestrator` loop (neither feature needs it beyond what's described).
- Known pre-existing issue: `ChatViewModel.cs` contains a literal NUL byte in the `ManifestFileId` string (~offset 86200) which makes some tools treat the file as binary. If you must edit that file, replace the raw NUL with the `"\0"` escape first as a tiny separate fix.

## Acceptance

- `dotnet build` clean, `dotnet test` green (including the new tests).
- Manual check: ask the agent to "อ่านหน้า https://example.com แล้วสรุป" → web_fetch returns readable text without consent prompt in auto mode.
- Ask the agent to "จำไว้ว่า user ชอบรายงานแบบสั้น (global)" → note appears in a NEW conversation's context and in MemoryView, and survives app restart.
