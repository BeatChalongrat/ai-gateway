# DESKTOP FIX — PHASE 4: Progressive streaming markdown + syntax highlighting

You are working on **SranDesktop** (Windows WPF, .NET 8, at `SranDesktop/`). Desktop-only — no gateway or chat-edge changes. Phases 1–3 are merged; do not regress them (especially: the draft-save DispatcherTimer from Phase 3 runs on the UI thread — keep new rendering work light enough not to starve it).

## Current behavior (verified in code — read these before starting)

- `Controls/MarkdownViewer.cs` (105 lines): while `IsStreaming` is true it does **not parse at all** (`OnMarkdownChanged` just sets `_dirty`, lines 53–58); when streaming ends, `OnIsStreamingChanged` renders the ENTIRE document once (lines 74–83). Non-streaming updates are throttled by a 66 ms `DispatcherTimer`. `RenderNow()` = `Content = Renderer.Render(Markdown)` — full rebuild every time.
- `Controls/MessageBubble.xaml`: while streaming, a plain `TextBlock` bound to the raw content is visible (lines ~445–465, `MultiDataTrigger` on `IsStreaming && !IsError`) and the `MarkdownViewer` is collapsed (`DataTrigger` at ~477). When streaming ends they swap in one frame — the "pop".
- `Controls/MarkdownRenderer.cs` (534 lines): Markdig `UseAdvancedExtensions`, renders to a `StackPanel` of block elements. `RenderCodeBlock` (lines 111–231) renders code as a **single-color** `TextBlock` (`CodeTextBrush`). All colors go through `SetResourceReference` for live theme switching — any new colored element MUST do the same.
- `ViewModels/MessageViewModel.cs`: `AppendContent` rebuilds `Content` from a `StringBuilder` per delta (line 149); `RenderContent => _displayContent ?? Content` (line 59) — `_displayContent` is set AFTER completion by `ChatViewModel.ExtractPlanBlocks` (line 1283), which strips `:::excel-plan / :::doc-plan / :::pptx-plan` and `:::artifact[...]` fences and builds cards. **During streaming the raw fence text is visible in the plain TextBlock** — that's the flicker to fix.
- `ChatViewModel.RaiseThrottledActivity` (~line 745) throttles auto-scroll to ~30 fps; `ChatView.xaml.cs` pauses auto-scroll when the user scrolls up. Progressive rendering changes block heights mid-stream — verify you don't break either.
- `SranDesktop.Presentation.csproj`: Markdig 0.37.*, CommunityToolkit.Mvvm, WebView2 — minimal deps.
- Known dead shortcuts (verified): `ShortcutsDialog.xaml` advertises **Esc = Stop generating** and **Ctrl+Shift+S = Toggle sidebar**, but `MainWindow_PreviewKeyDown` (`Views/MainWindow.xaml.cs`) handles neither.

## Ground rules

- Read every file you touch IN FULL first.
- Every new color/brush via `SetResourceReference` with tokens added to BOTH `Resources/Themes/Dark.xaml` and `Light.xaml`.
- New user-facing strings → BOTH `En`/`Th` in `Dict.cs` (`DictParityTests` enforces).
- Pure logic must live in testable classes; WPF-element code should be a thin shell over it. Tests for pure parts are REQUIRED; if you add UI-element tests, WPF requires STA (use `[StaFact]`-style via a small helper or skip element tests and cover logic only — your call, state it).
- Finish state: `dotnet build` 0 errors / 0 new warnings, `dotnet test` all green.

---

## TASK 1 — Progressive block-level rendering during streaming

Replace the plain-text streaming seam with incremental markdown rendering. Design constraints, then implementation outline:

**Constraints**
- Markdig PARSING of a whole message is cheap; building WPF element trees is the expensive part. The optimization target is: **re-build only blocks whose source text changed** (in practice: the last block, plus any block newly appeared).
- An unterminated fenced code block mid-stream parses as a code block to end-of-text in Markdig — that's fine and desirable (code renders progressively inside its styled container).
- Render cadence while streaming: at most every **150 ms**, on `DispatcherPriority.Background` (token appends and the Phase 3 draft timer must win over re-renders). Keep the existing 66 ms behavior for non-streaming updates. Final render on `IsStreaming → false` stays unthrottled and authoritative (it also clears any streaming artifacts of the incremental path).

**Implementation outline**
1. New pure class `Controls/Markdown/BlockDiff.cs` (or similar, Presentation layer): given the previous list of block source-spans (start, length, text-hash) and the new markdown string parsed into blocks, return: index of first changed block, list of blocks to (re)render. Blocks strictly before the first changed index are reusable. Use Markdig's `Block.Span` for source extents. THIS CLASS IS UNIT-TESTED (no WPF types in it).
2. `MarkdownRenderer` gains `RenderBlockPublic`-style access (or refactor `RenderBlock` to internal) so the viewer can render one block at a time. Rendering a single block must produce identical output to the full-document path (same method).
3. `MarkdownViewer` streaming mode: keep a `StackPanel` as `Content`; on each tick, run the diff, remove children from the first-changed index onward, append re-rendered blocks. `_dirty` flag machinery replaced accordingly. On `IsStreaming → false`, do ONE full clean render (existing `RenderNow`) — never trust the incremental state as final.
4. `MessageBubble.xaml`: the plain streaming `TextBlock` and its triggers are REMOVED; `MarkdownViewer` becomes visible during streaming (keep the `IsError` collapse trigger). `ShowTypingIndicator` behavior (typing dots before first token) must be preserved.
5. Auto-scroll: after each incremental render, behavior must match today's contract — pinned-to-bottom follows growth, user-scrolled-up stays put. Test manually with a long streamed table + code block; describe the result in your summary.

## TASK 2 — Syntax highlighting in code blocks

**No heavyweight editor dependency.** Two acceptable approaches — pick ONE and justify in your summary:
- (a) `ColorCode.Core` NuGet package (regex grammars, maintained, no UI deps) + a small custom formatter that maps its scope names to WPF `Run`s; or
- (b) a hand-rolled tokenizer `Controls/Markdown/SyntaxHighlighter.cs` covering: keywords, strings (incl. interpolation quotes), comments (line + block), numbers, for these languages: `csharp, python, javascript, typescript, json, xml/html, sql, bash/shell, go, java, yaml, css`. Unknown language → current single-color rendering.

Either way:
- Output is a pure `IReadOnlyList<(string text, TokenKind kind)>` from a testable class — the WPF layer maps `TokenKind` → brush.
- New theme tokens in BOTH theme files: `SynKeywordBrush`, `SynStringBrush`, `SynCommentBrush`, `SynNumberBrush`, `SynTypeBrush` (pick dark/light values that read well on the existing `CodeBgBrush`; keep contrast ≥ WCAG AA against it). ALL via `SetResourceReference` so theme switching restyles live.
- `RenderCodeBlock` uses it for both streaming and final renders. **Perf guards:** skip highlighting for a block > 20,000 chars during streaming (plain render, highlight on the final pass) and > 200,000 chars always.
- Copy button must still copy the RAW code (not formatted text) — it already closes over the `code` string; keep it that way.
- Tests: tokenizer cases per language family — keywords vs identifiers-containing-keywords (`classify` is not `class`), string containing `//`, comment containing quotes, JSON numbers/bools, unknown language passthrough.

## TASK 3 — Hide incomplete plan/artifact fences during streaming

Today the raw `:::excel-plan {json...}` text streams into view, then vanishes when cards are extracted at completion.

1. New pure function (testable), e.g. `StreamingFenceGuard.TruncateAtOpenFence(string markdown)`: if the text contains an **opening** plan/artifact fence (`:::excel-plan`, `:::doc-plan`, `:::pptx-plan`, `:::artifact[`) whose closing `:::` has not arrived yet, return the markdown truncated just before the fence plus a flag. Reuse the exact fence patterns from `ChatViewModel` (`PlanFenceRegex`/`ArtifactFenceRegex` — move/share the pattern constants rather than duplicating; they currently live as compiled regexes in `ChatViewModel`).
2. `MarkdownViewer` (streaming mode only) passes its input through the guard; when the flag is set, append a small indicator chip element below the rendered blocks: spinner-dot + localized text — Dict keys `stream.preparingDoc` (EN `"Preparing a document…"` / TH `"กำลังเตรียมเอกสาร…"`) for plan fences and `stream.preparingArtifact` (EN `"Building an artifact…"` / TH `"กำลังสร้างชิ้นงาน…"`) for artifact fences.
3. A COMPLETED fence mid-stream (closing `:::` already arrived) stays hidden too — keep truncating at the first fence opening; the real cards appear on completion via the existing `ExtractPlanBlocks` path. (Simplest correct rule; fence content is never useful to display raw.)
4. Final render is untouched: `RenderContent` already has fences stripped by then.
5. Tests: no fence → unchanged; open unterminated plan fence → truncated + flag(doc); artifact fence → flag(artifact); completed fence mid-text → still truncated at opening; text after a completed fence is NOT shown during streaming (documented behavior); `DictParityTests` green.

## TASK 4 — Honor the advertised shortcuts (small)

In `MainWindow_PreviewKeyDown` (`Views/MainWindow.xaml.cs`):
- **Esc**: if a stream is active (ChatViewModel `IsStreaming` / the same state the composer Stop button uses), invoke the same `CancelStreamingCommand`. Must NOT swallow Esc when a dialog/popup is open or search is focused — check the existing Esc handling order and only handle it when nothing else claims it.
- **Ctrl+Shift+S**: toggle the sidebar (same command the toolbar button uses).
`ShortcutsDialog` already advertises both — no dialog changes needed.

---

## Final verification checklist

1. `dotnet build` 0/0; `dotnet test` all green including new tokenizer/diff/fence tests and `DictParityTests`.
2. Trace review of `MarkdownViewer` top-to-bottom: (a) streaming ticks ≤ every 150 ms at Background priority, (b) final render is a full clean rebuild, (c) non-streaming path behaves exactly as before (66 ms coalescing, first render immediate).
3. Describe in the summary, step by step, what a user SEES for this streamed message: two paragraphs → a ```python code block → a `:::doc-plan` fence → closing text. (Expected: paragraphs appear and format progressively; the code block appears with highlighting as it streams; the moment the fence opens, output pauses at the chip "Preparing a document…"; on completion the plan card + remaining text appear.)
4. Confirm the perf guards and where they kick in.
5. Summarize changes file by file + list follow-up risks for Phase 5 (backend wiring: server cancel, fallback_info chip, feedback POST, context-info nudge, full-text search) WITHOUT implementing them.
