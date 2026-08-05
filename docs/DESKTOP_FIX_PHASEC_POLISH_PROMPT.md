# DESKTOP FIX — PHASE C: Desktop consumer polish (close Track-1 to feel like Claude Desktop)

Final desktop-only, fully-verifiable phase. The agentic core (Phase A) and depth (Phase B) are done; this closes the remaining **visible polish gaps** the 3-model audit flagged repeatedly — the rough edges that make it feel a step below Claude Desktop / ChatGPT even though the engine is strong. Everything here is testable with `dotnet build` + `dotnet test` (no live infra). Do not regress Phases 1–16 + A + B. Do NOT `git push`.

Independent PARTs — do/commit separately, each with R0 + tests where logic is extractable (pure helpers) and a stated visual check where it's XAML-only. `dotnet build` 0/0, `dotnet test` green, existing suites unmodified, `DictParityTests` green for any new EN/TH strings.

---

## PART 1 — Render inline markdown images (biggest visual divergence from all three peers)

**Verified across audits:** `MarkdownRenderer.cs` (~L526-542) draws inline markdown images `![alt](url)` as an **icon + link**, not the actual image. Claude/ChatGPT/Gemini all render inline images. This is the single most noticeable "less polished" gap in the desktop chat.

R0: read `MarkdownRenderer.cs` fully — the inline-image branch, how it currently emits icon+link, how blocks/inlines build WPF elements, and how the artifact/math WebView2 controls load bundled/local assets (for the CSP/loading pattern).

1. **Render the image inline** when the markdown has `![alt](src)`:
   - **`data:` URIs** (base64 images the model or a tool emits — e.g. a chart) → decode and render as a WPF `Image` inline. This is the common agent case (run_python charts, generated images).
   - **`https://` URLs** → render via `BitmapImage` with async load, a sane max size (constrain width to the message column, preserve aspect), a loading placeholder, and a graceful fallback to the current icon+link on load failure or non-image content. Respect the existing security posture — no arbitrary local file loads from markdown (only data: and http(s)); a `file://` or local path stays as a link, not an auto-load.
   - **`appassets`/local bundled** if applicable — match the artifact control's pattern.
2. Keep alt text as accessibility (`AutomationProperties.Name`) and as the fallback caption.
3. Size/perf: cap decoded image dimensions; don't let a huge image blow layout or memory. Lazy where possible.
4. Tests: the `data:`-URI path decodes to a renderable image element (pure helper: parse `![](data:image/png;base64,...)` → byte[]/decision, unit-tested); malformed/oversized data URI → falls back to link, no throw; a non-image URL stays a link. (The actual WPF `Image` render is a visual check — state it; test the parse/decision logic.)

## PART 2 — Jump-to-latest button (ChatGPT/Claude both have it)

**Verified:** `ChatView.xaml`/`ChatView.xaml.cs` — auto-scroll pauses when the user scrolls up (good, Phase 4), but there's **no "scroll to bottom / N new messages" button** to get back. Both peers show one when you've scrolled away during streaming.

R0: read `ChatView.xaml.cs` (the scroll/auto-scroll logic — where "user scrolled up" is detected, the pinned-to-bottom state) and `ChatView.xaml`.

1. Show a floating "jump to latest" button (down-chevron, bottom-right of the message list) **only when** the user is scrolled up AND content is below (i.e., not already pinned to bottom). Clicking it smooth-scrolls to the newest message and re-pins. During streaming while scrolled up, optionally show a subtle "new messages" affordance on the same button.
2. Respect reduced-motion (Phase 8 `MotionSettings`) — snap instead of smooth-scroll when off. Themed, consistent with existing controls, `AutomationProperties.Name`.
3. Tests: the visibility predicate is pure-testable — extract `ShouldShowJumpToLatest(isPinnedToBottom, hasContentBelow)` (or similar from the real scroll state) and unit-test the states; the scroll action itself is a visual check.

## PART 3 — Consent danger banner must cover the high-blast-radius tools

**Verified (`ToolConsentViewModel.cs` ~L10-11):** the `IsDangerous`/danger-styling set is a hardcoded ~3-tool list + Shell — it **misses `run_python`, ALL computer-use tools (click/type/key/scroll/drag/screenshot/open_application), and every remote MCP tool** — exactly the highest-blast-radius actions, where the warning is absent. A user approving a computer-use or remote-MCP action gets the same calm UI as reading a file.

R0: read `ToolConsentViewModel.cs`, how `IsDangerous` drives the banner styling in the consent dialog XAML, and the tool categories (`ToolCategory`) so danger can be derived from category, not a brittle name list.

1. **Derive danger from capability, not a hardcoded name list:** a tool is "dangerous" (warrants the prominent banner) if it can affect the machine or the outside world — Shell (`run_command`), the sandboxed-but-still-code `run_python`, ALL computer-use (Win32 input/screen), file WRITES (`write_file`/`edit_file`/`create_directory`/`move_file`), and **remote MCP tools** (arbitrary third-party/local-process side effects). File READS and plan/notes stay calm. Base it on `ToolCategory` + a small explicit set so a new tool is dangerous-by-default if it's in a side-effecting category (fail-safe, not fail-open).
2. The banner copy should briefly say WHY (e.g. "runs a command on your computer", "controls your mouse/keyboard", "calls an external connector"). Localized EN/TH.
3. This is a security-UX tightening — it only ADDS warnings, never removes a consent gate. Confirm no tool that currently prompts stops prompting.
4. Tests: `IsDangerous` (or the derivation) returns true for run_command, run_python, each computer-use tool, write/edit tools, and a remote MCP tool; false for read_file/list_directory/search_files/update_plan/remember; a hypothetical new side-effecting-category tool defaults to dangerous.

## PART 4 — Model picker keyboard navigation + per-model descriptions

**Verified (both apps flagged):** the model picker rows are click-only (`<div role="option" onClick>` on web; likely similar mouse-only on desktop) — no arrow-key/roving-tabindex navigation, and per-model descriptions that exist in the data are unrendered. Claude/ChatGPT model pickers are fully keyboard-navigable.

R0 (desktop scope this phase — web can follow): read the desktop model-picker (`ModelPicker`/model-selector control + its ViewModel), how models + descriptions are bound, and the existing keyboard-handling patterns (e.g. `MainWindow_PreviewKeyDown` from Phase 4).

1. **Keyboard nav:** Up/Down moves selection through the list (roving focus), Enter selects, Esc closes, Home/End jump. Focus is visible (Phase 8 focus ring). The picker traps focus while open and restores it on close.
2. **Render per-model descriptions** (context window, provider, a one-line capability note) that already exist in the model data but aren't shown — like the peers' pickers.
3. Themed, `AutomationProperties` on rows, consistent with existing popup styling.
4. Tests: the selection-index logic is pure-testable (given a list + current index, Up/Down/Home/End compute the next index, wrapping or clamping per your choice) — unit-test it; the visual focus/render is a stated visual check.

## PART 5 — System/Auto theme + small consistency sweep

**Verified:** no System/Auto theme tri-state (dark/light only); a few native `MessageBox.Show`/default-chrome dialogs and hardcoded `Brushes.*` remain that break the themed aesthetic (audit noted ~6 native dialogs + `StatusBrush` hardcoded won't recolor in light).

R0: read `ThemeManager`/theme settings, `UserSettings` theme field, and grep for `MessageBox.Show` + `Brushes.` (hardcoded WPF brushes) in Presentation.

1. **System/Auto theme:** add a third theme option that follows the OS light/dark setting (listen for the OS theme-change and re-apply live). Default can stay explicit; add Auto as an option. R0: WPF reads the OS theme via registry/UISettings — pick the reliable approach.
2. **Consistency sweep (small, bounded):** replace the remaining native `MessageBox.Show` confirmations with the app's themed dialog (the pattern already exists from earlier phases); fix hardcoded `Brushes.*` (e.g. `StatusBrush`) to use `DynamicResource` theme brushes so they recolor in light mode. Don't chase every pixel — just the specific audit-named offenders.
3. Tests: theme-selection persistence round-trip; Auto resolves to the OS value (mockable via the resolver abstraction); `DictParityTests` for any new strings. Visual checks stated for the dialog/brush swaps.

---

## Final verification checklist
1. `dotnet build` 0/0; `dotnet test` green (new pure-logic tests for image-parse / jump-visibility / danger-derivation / picker-index / theme-resolve + `DictParityTests`; Phases A/B + earlier suites unmodified).
2. **Polish walk (written):** confirm each now matches the peers — an inline chart image renders in the chat (not a link); scrolling up during a stream shows a jump-to-latest button that re-pins; approving a computer-use or run_python action shows the danger banner; the model picker is arrow-key navigable with descriptions; Auto theme follows the OS. State honestly what's now at-parity vs any residual.
3. **Security:** PART 1 image loading doesn't auto-load local `file://` paths from markdown (only data:/http(s)); PART 3 only ADDS consent warnings (no gate removed); nothing weakens PathGuard/DLP/consent.
4. Summarize changes file by file. This closes the desktop Track-1 (agentic core + depth + polish). Note what's left: the live-infra track (17B sandbox broker, 17C NER/Thai, 18 voice/OTel/connectors/code-UI) and ops (19 CD/IaC/k8s), plus the operational items (JWT rotation, git push) — none desktop-only, all needing an environment or deploy access.
