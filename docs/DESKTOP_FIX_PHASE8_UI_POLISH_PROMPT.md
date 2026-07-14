# DESKTOP FIX — PHASE 8: UI polish — Claude Desktop / ChatGPT-level feel

Final phase. **SranDesktop only** (WPF, .NET 8). Phases 1–7 + hotfixes are merged; every existing test stays green and unmodified (except where a task explicitly extends one).

## The goal, stated as feel

A user coming from Claude Desktop or ChatGPT should notice nothing missing and nothing janky: the sidebar scrolls like silk with 500 conversations, messages slide in softly, every action confirms itself (toast), every control matches the theme in both light and dark, drag a chat onto a folder and it just works, keyboard and screen-reader users can reach everything, and nothing ever flashes stale content. Polish is the feature.

**Hard feel-budget (verify each in the summary):** all new animations 120–200 ms with ease-out, never blocking input; zero layout jank while streaming (no scroll jumps when blocks re-render); sidebar smooth with 500+ conversations; all animations respect the OS reduced-motion setting.

## Known gaps this phase closes (all previously verified; re-verify exact positions in R0 — line numbers have drifted across Phases 4–7)

1. **No toast system** — success/failure of copy, export, connector OAuth (the Phase 7 deep link arriving while the window is backgrounded gives no feedback), sync errors: all silent or buried in a sidebar status line.
2. **Sidebar is not virtualized** — folders + conversations are plain `ItemsControl`s inside one `ScrollViewer` (`SidebarView.xaml`); every row realizes at load. No drag-drop to folders (context-menu submenu only).
3. **Unstyled default-chrome controls** — `Styles.xaml` has NO custom style for `RadioButton`, `Slider`, `Expander`, or `ToolTip`; they render light-gray default WPF chrome that breaks dark mode (FirstRunDialog radios, VoiceSettingsDialog slider, MessageBubble reasoning Expander, CrashDialog expander, every tooltip).
4. **Hardcoded colors bypassing themes** — MainWindow usage popup `#3B82F6` bars + `#10B981` credits dot (should be `AccentBlueBrush`/`AccentGreenBrush`).
5. **Accessibility holes** — `AutomationProperties.Name` exists in only 3 files (MainWindow/SidebarView/ComposerControl); every icon-only button in MessageBubble (copy/thumbs/regenerate/read-aloud/branch), tool cards, PlanCard, ArtifactControl toolbar, KB/Memory/Skills/Connectors views is unnamed. Buttons have no visible keyboard-focus indicator (only TextBoxes have a focus ring).
6. **Empty states missing** on Skills and Connectors views (bare ItemsControl).
7. **Inconsistencies:** `InputDialog` uses `WindowStyle="ToolWindow"` (siblings use styled normal windows); `FirstRunDialog` has a permanently-disabled "Show system tray icon" checkbox (dead control).
8. **Recycled-container stale flash (Phase 4 note):** the message list uses `VirtualizationMode=Recycling`; a `MarkdownViewer` recycled onto a NEW streaming message can show the previous message's rendered Content for up to ~150 ms until the first stream tick replaces the panel.
9. **Search-hit pull (Phase 5 deferral):** a message-search hit whose conversation exists only server-side (never pulled) is silently hidden by `SearchHitResolver` (case (c) returns null).

## Ground rules

- Read every file you touch IN FULL; do R0 first and answer it in the summary.
- Every color via theme resources (`DynamicResource`/`SetResourceReference`) in BOTH `Dark.xaml` and `Light.xaml`. Every new user-facing string in BOTH `En`/`Th` (`DictParityTests`). Natural Thai.
- Respect reduced motion: gate every new animation on a single helper (e.g. `MotionSettings.Enabled` reading `SystemParameters.ClientAreaAnimation`); when off, states snap instantly.
- Pure logic (toast queue, drag-drop decision, pull-resolution) in testable classes with tests. Visual-only changes need no tests but DO need the R0-verified before/after described in the summary.
- Finish state: `dotnet build` 0/0; `dotnet test` all green.

## R0 — Reconnaissance (answer all)

1. `SidebarView.xaml` current structure (the ScrollViewer/ItemsControl nesting, the grouped time buckets, where the Phase 5 "In messages" section sits) and `ConversationListViewModel`'s collections (what would virtualization + grouping need — `CollectionViewSource` with groups, or the existing manual bucket lists?).
2. `Styles.xaml`: confirm which control types have styles today; list the exact windows/controls that will pick up the new RadioButton/Slider/Expander/ToolTip styles.
3. MainWindow usage popup: exact hardcoded-color positions.
4. `MarkdownViewer` + `MessageBubble` recycling path: what happens on container recycle today (DataContext change order vs `IsStreaming`/`Markdown` DP changes) — confirm the stale-flash mechanism before fixing it.
5. `ChatView.xaml.cs` auto-scroll implementation (needed to keep the feel-budget "no scroll jumps" promise when adding message-entrance animation).
6. Where toasts can mount: MainWindow layout root (an overlay layer above content, below dialogs).
7. `IChatEdgeApiClient.GetConversation`/list-messages methods available for Task 6 (server-conversation pull) and how `ConversationSyncService` upserts a server conversation locally.

---

## TASK 1 — Toast system (one, small, used everywhere it matters)

1. New `ToastService` (Application layer, testable): `Show(ToastKind kind, string message, TimeSpan? duration = null)`; queue with max 3 visible, newest at bottom; auto-dismiss (info/success 3 s, error 6 s); dedupe identical consecutive messages.
2. `ToastHost` control mounted once in `MainWindow` (bottom-center or bottom-right — match the app's visual language), themed card + icon per kind, fade/slide in-out within the motion budget, click-to-dismiss, `AutomationProperties.LiveSetting="Polite"` so screen readers announce them.
3. Wire ONLY these call sites (no toast spam): copy-to-clipboard actions (message copy, code copy — the code-block button currently shows its inline check: KEEP that, no toast needed there; message-level copy gets a toast), export saved (md/json), connector OAuth result arriving via deep link (success + failure — this also covers the window-was-closed case: activation + toast), agent-sync failure escalations (the Warning-level per-conversation failures from Phase 3 hotfix — throttle to one toast per session), update-downloaded notification if one exists.
4. Dict keys for every toast text, EN + natural TH.
5. Tests: queue ordering, cap, dedupe, per-kind duration, dismiss.

## TASK 2 — Sidebar: virtualization + drag-drop to folders

1. **Virtualize** the conversation list: restructure so rows come from ONE items control with `VirtualizingStackPanel` (`IsVirtualizing`, `Recycling`, pixel scroll) — grouping via `CollectionViewSource.GroupDescriptions` + `VirtualizingStackPanel.IsVirtualizingWhenGrouping="True"`, or keep the manual buckets but flatten into a single ObservableCollection of row models (header rows + conversation rows) — pick whichever preserves the existing visuals exactly (R0.1 decides; justify). Selection, inline rename, context menus, pin/folder behavior, and the Phase 5 search sections must all survive — the R2/R3 parity screenshots describe the target look.
2. **Drag-drop:** drag a conversation row onto a folder row (or onto "remove from folder" space at the list top) → same action as the existing Move-to-folder menu. Visual affordances: drag ghost/opacity on the source, highlight on the drop target, and it must not fight click/rename (start drag only after the system drag threshold — `SystemParameters.MinimumHorizontalDragDistance`). Keyboard/менu paths remain (drag-drop is an addition, not a replacement).
3. Pure helper + tests: `SidebarDropDecision.Decide(draggedConversationId, targetKind, targetId)` → move/no-op (e.g., dropping onto its current folder, dropping a folder onto itself is rejected at the source — folders are NOT draggable this phase).
4. Feel check for the summary: describe scroll behavior at 500 seeded conversations (row realization count via VisualTreeHelper spot-check or reasoned from virtualization mode).

## TASK 3 — Motion polish + the stale-flash fix

1. **Message entrance:** new messages (user send + assistant placeholder) fade-in + 8 px slide-up, 140 ms ease-out, gated on the motion helper. History loads do NOT animate (only messages appended while the conversation is open — the distinction matters; find the seam in how Messages is populated).
2. **Auto-scroll:** keep the existing contract (pinned follows, user-scrolled-up stays). If the entrance animation causes visible scroll fighting (R0.5), anchor the scroll after the animation frame rather than before — describe what you did.
3. **Stale-flash fix (R0.4):** when `MarkdownViewer` is recycled onto a different message (DataContext change) or `IsStreaming` flips true, clear `Content` immediately (empty panel) before the first tick renders — a recycled viewer must never show the previous message's content. Add this to the reset path; the typing-dots indicator already covers the visual gap.
4. Popup/menu animations already exist — do not touch them.

## TASK 4 — Theme & style completeness

1. `Styles.xaml`: add themed styles for `RadioButton` (accent dot, focus ring), `Slider` (thumb + track in theme colors), `Expander` (chevron rotation animation within motion budget, themed header), and a global `ToolTip` style (dark elevated card in dark mode, matching light equivalent). All `DynamicResource`; verify each affected window (R0.2 list) in both themes.
2. Replace the MainWindow usage-popup hardcoded `#3B82F6`/`#10B981` with `AccentBlueBrush`/`AccentGreenBrush`.
3. `InputDialog`: drop `ToolWindow` style, match sibling dialogs (`DarkTitleBarHelper` etc.).
4. `FirstRunDialog`: remove the dead disabled checkbox (or make it functional if the underlying setting exists — R0 decides which is honest).
5. Skills + Connectors views: empty states matching the KB view's pattern (icon + one-line hint + CTA where sensible). Dict keys EN/TH.

## TASK 5 — Accessibility pass

1. `AutomationProperties.Name` (localized via the existing `{loc:T}` mechanism where XAML-set) on every icon-only interactive control: MessageBubble action row (copy, thumbs up/down, regenerate, read-aloud, branch prev/next, edit), tool-execution card toggles + retry, PlanCard buttons, ArtifactControl toolbar (copy/fullscreen/open-external), composer icon buttons (attach, mic, send/stop), sidebar row overflow buttons, KB/Memory/Skills/Connectors/ScheduledTasks action buttons, window chrome buttons if unnamed. Sweep systematically; list the count per file in the summary.
2. **Keyboard focus visibility:** a `FocusVisualStyle` (theme-aware ring) applied through the base Button styles so Tab focus is visible on every button — today only text inputs show focus.
3. **Message actions by keyboard:** make message bubbles focusable (`Focusable=true`, `IsTabStop` on the container or an invisible header button) so Tab reaches each message and its action row; the action row already shows on `IsLastAssistantMessage` — extend its visibility trigger to `IsKeyboardFocusWithin` so keyboard users see the actions they've reached.
4. Toasts announced politely (Task 1). No other screen-reader work this phase (full narrator pass is out of scope — say so honestly in the summary).

## TASK 6 — Search-hit pull (close the Phase 5 deferral)

When `SearchHitResolver` finds no local match (case (c)): instead of hiding the hit, show it normally; on click, **pull the conversation** — fetch it via the existing chat-edge client (R0.7), upsert it locally as a server-source conversation (reuse the `ConversationSyncService` upsert path so `server_id`/`source` semantics stay consistent), then open it. Loading affordance on the row while pulling; failure → error toast (Task 1) and the row stays. Pure resolution change + a small async pull path; tests for the resolver's new three-way result (open-local / pull-then-open / —) and the pull path with a mocked client.

---

## Final verification checklist

1. `dotnet build` 0/0; `dotnet test` all green (new toast/drop-decision/resolver tests + `DictParityTests`; zero existing tests modified except the Task 6 resolver tests which may be extended).
2. **Feel-budget audit** (written): each new animation's duration/easing/reduced-motion gate; the streaming-jank statement (entrance animation + Phase 4 progressive rendering coexisting without scroll fights); sidebar virtualization evidence.
3. **Both-themes sweep:** list every view touched and confirm dark AND light rendering (call out anything you could not visually verify — no live session — and what you did instead, e.g. resource-reference audit).
4. Accessibility summary: named-control count per file, focus-ring coverage, the honest out-of-scope note.
5. Changes file by file. This is the LAST phase of the program — end the summary with a short program-wide regression statement: every suite from Phases 1–7 still green, and the final total test count.
