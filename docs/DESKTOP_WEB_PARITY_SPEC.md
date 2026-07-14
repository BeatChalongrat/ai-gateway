# SRAN Desktop — Web Parity Spec v2

Source of truth: **live** web app at `http://localhost:3002/chat` (logged in as
`somchai@acme.ai.gateway`), values extracted via Chrome DevTools
`getComputedStyle` / CSS custom properties — every hex and px below is measured,
not eyeballed. Audited 2026-07-07.

Desktop reference: `SranDesktop/` (WPF). This spec lists the exact deltas to
reach 1:1 parity, with copy-pasteable XAML, then the agentic-feature specs.

---

## 1. Color tokens (measured from `:root` / `[data-theme]`)

| Token (web var) | Desktop brush key | Dark (web) | Light (web) | Desktop now | Match? |
|---|---|---|---|---|---|
| `--bg-primary` | BgPrimary | `#212121` | `#ffffff` | dark #212121 / light #ffffff | ✅ |
| `--bg-sidebar` | BgSidebar | `#171717` | `#f9f9f9` | #171717 / #f9f9f9 | ✅ |
| `--bg-surface` | BgSecondary | `#2f2f2f` | `#f0f0f0` | #2f2f2f / #f0f0f0 | ✅ |
| `--bg-surface-hover` | BgSelected | `#3a3a3a` | `#e5e5e5` | #3a3a3a / #e5e5e5 | ✅ |
| `--text-primary` | TextPrimary | `#ececec` | `#0d0d0d` | ✅ | ✅ |
| `--text-secondary` | TextSecondary | `#a0a0a0` | `#5b5b5b` | ✅ | ✅ |
| `--text-tertiary` | TextTertiary | `#6b6b6b` | `#8a8a8a` | ✅ | ✅ |
| `--text-placeholder` | (uses TextSecondary) | `#8e8e8e` | `#9b9b9b` | approx | ⚠ minor |
| `--accent` | AccentBlue* | `#ececec` (=text) | `#0d0d0d` (=text) | **#60a5fa / #3b82f6 (blue)** | ❌ **web has NO blue** |
| `--border-subtle` | — | `rgba(255,255,255,.08)` | `rgba(0,0,0,.06)` | — | ➕ add |
| `--border-default` | Border | `rgba(255,255,255,.12)` | `rgba(0,0,0,.10)` | solid #3a3a3a / #e5e5e5 | ⚠ close |
| `--success` | AccentGreen | `#10a37f` | `#10a37f` | ✅ | ✅ |
| `--error` | AccentRed | `#ef4444` | `#ef4444` | ✅ | ✅ |
| `--warning` | AccentWarning | `#f59e0b` | `#f59e0b` | ✅ | ✅ |
| `--user-bubble-bg` | UserBubble | `#2f2f2f` | `#f0f0f0` | ✅ | ✅ |
| `--sidebar-item-active` | BgSelected | **`#2a2a2a`** | `#e5e5e5` | dark **#3a3a3a** | ❌ dark off |
| `--code-bg` | CodeBg | **`#1e1e1e`** | **`#f7f7f8`** | **#1a1a1a fixed both** | ❌ |
| `--code-text` | CodeText | **`#d4d4d4`** | **`#1f1f1f`** | **#e2e8f0 fixed** | ❌ |
| `--code-header-bg` | (none) | `#2d2d2d` | `#eee` | (none) | ➕ add |

\* **Big finding:** the web `--accent` is neutralized to the foreground color
(`#ececec` dark / `#0d0d0d` light) — the web app uses **no blue/teal accent
anywhere**. The only chromatic color is `--success #10a37f`. The desktop still
uses AccentBlue for the logo, New-chat, send button, and agent-toggle. See Fix 1.

---

## 2. Typography (measured)

| Element | Font | Size | Weight | Line-height | Color |
|---|---|---|---|---|---|
| Greeting (empty state) | system sans | **30px** | 600 | 36px | text-primary |
| Subtitle "How can I help…" | system sans | **18px** | 400 | 28px | text-secondary |
| Composer placeholder | system sans | **15px** | 400 | — | text-placeholder |
| **User message** | system sans | **15px** | 400 | **24.4px (1.625)** | text-primary |
| **Assistant prose** | system sans | **16px** | 400 | **28px (1.75)** | text-primary |
| Sidebar section label | system sans | **10px** | 500 | — | text-tertiary, UPPERCASE |
| Sidebar conversation item | system sans | **13px** | 400 | — | text-secondary |
| New chat button | system sans | 14px | 400 | — | text-primary |
| Sidebar wordmark | **Outfit (brand)** | 16px | 700 "SRAN" + 400 "Chat" | — | text-primary |
| Suggestion chip | system sans | **13px** | 400 | — | text-secondary |
| Disclaimer | system sans | **11px** | 400 | — | text-tertiary |
| Assistant meta line | system sans | 11px | 400 | — | text-tertiary |
| Code block | **JetBrains Mono** → Cascadia Code | 13px | 400 | — | code-text |

Fonts: `--font-brand` = **Outfit**; `--font-sans` = system (`Segoe UI` on
Windows); `--font-mono` = **JetBrains Mono** (Desktop substitutes Cascadia Code).

---

## 3. Spacing / sizing (measured)

| Element | Value |
|---|---|
| SRAN logo (empty state) | image `sran-logo.png`, **96×96** (md), 80px (sm); black art, CSS-inverted to white in dark |
| Composer container | max-width **720px**, radius **24px**, border **0.8px** border-default, shadow **0 2px 12px rgba(0,0,0,.18)**, bg surface |
| Composer placeholder | "Message SRAN Chat…" |
| User bubble | radius **20px**, padding **10px 16px**, max-width **85%**, right-aligned, shadow-sm |
| Assistant message | full-width (max 768px column), no bubble, no avatar |
| Assistant action button | **30×30**, radius 4px, padding 6px, **18px** icon, text-tertiary |
| Send button | **32×32 circle** (radius 9999px); disabled bg surface-hover / icon text-tertiary; **active bg = text-primary, icon = bg-primary** (foreground-inverted), svg 16px |
| Model selector | pill (radius 9999px), height 24px, "✦ Auto ⌄", text-secondary |
| Suggestion chip | **89×37**, radius 9999px (full pill), padding **8px 16px**, gap 8px, border **0.8px border-subtle**, transparent bg, in a **horizontal row** below the composer |
| Sidebar header | height 48px, wordmark padding 0 12px + collapse chevron `<` |
| New chat button | height 38px, radius 8px, padding 8px 12px, **transparent bg + 0.8px border-subtle (outlined)**, shortcut `⌘⇧N` |
| Sidebar section label | padding 8px 12px 4px |
| Sidebar conversation item | height **34px**, radius 8px; select = bg surface-hover only (**no accent bar**); kebab `⋯` on hover |
| User area (sidebar bottom) | avatar circle (initial) + email + kebab `⋯` |
| Token counter | bottom-right floating pill: green ring + number (e.g. `978,084`) + chevron |

---

## 4. Structural differences (web vs current desktop)

1. **Empty state layout.** Web: `logo(96) → greeting(30/600) → subtitle(18) →
   **composer (centered, in the empty state)** → disclaimer(11) → **chip row**
   (Write · Learn · Code · Analyze, full-pill, horizontal)`. Desktop: blue "S"
   square, greeting, subtitle, **2×2 chip grid**, and **no composer** (composer
   only appears once a conversation is open).
2. **Logo.** Web = `sran-logo.png` owl mascot with ® (inverted per theme).
   Desktop = blue "S" rounded square.
3. **New chat = outlined ghost**, not filled blue.
4. **No centered conversation title** in web header. Instead top-right shows a
   **Memory** toggle + a `⋯` kebab. Desktop centers the title.
5. **Selected sidebar item** = background highlight only (no left accent bar).
6. **Assistant action row** = always visible (copy · 👍 · 👎 · regenerate) below
   a completed message, left-aligned. Desktop shows it on hover.
7. **Code blocks are theme-aware** (light code in light theme). Desktop is
   always dark.
8. Web composer chrome adds **Knowledge (KB), image, video (clapperboard), mic**
   buttons; a **New folder** button; a **token/credit counter**; a **disclaimer**.

---

## 5. Fixes (prioritized)

### Fix 1 — Neutralize the accent (remove blue) — **P0, brand identity**
**Web:** `--accent` = foreground (`#ececec`/`#0d0d0d`); no blue anywhere; send
button inverts to foreground; New-chat is outlined; logo is the owl.
**Desktop now:** AccentBlue `#60a5fa`/`#3b82f6` used on New-chat fill, send
circle, agent-toggle checked, empty-state logo, tool-consent header.
**Change:** repoint the brand accent to a neutral "on-surface strong" token so
buttons/toggles read like the web. Add to **both** theme dictionaries:
```xml
<!-- Dark.xaml -->
<Color x:Key="AccentColor">#ECECEC</Color>
<Color x:Key="OnAccentColor">#212121</Color>
<!-- Light.xaml -->
<Color x:Key="AccentColor">#0D0D0D</Color>
<Color x:Key="OnAccentColor">#FFFFFF</Color>
<!-- both -->
<SolidColorBrush x:Key="AccentBrush" Color="{StaticResource AccentColor}" />
<SolidColorBrush x:Key="OnAccentBrush" Color="{StaticResource OnAccentColor}" />
```
Then in `Styles.xaml` `AccentButton` swap `AccentBlueBrush`→`AccentBrush`,
`TextOnAccentBrush`→`OnAccentBrush`. Keep `AccentBlueBrush` defined (some accents
like the agent-mode text may stay blue by choice), but the primary action buttons
should use the neutral accent to match web.

### Fix 2 — New chat button → outlined ghost — **P0**
**Web:** transparent bg, `0.8px` border-subtle, text-primary, radius 8px, `⌘⇧N`.
**Desktop now:** filled `AccentButton` (blue).
**Change (`SidebarView.xaml`):**
```xml
<Button Command="{Binding NewChatCommand}" Margin="12,0,12,8" Padding="10,9"
        Background="Transparent" BorderThickness="1"
        BorderBrush="{DynamicResource BorderBrush}"
        Foreground="{DynamicResource TextPrimaryBrush}"
        controls:ButtonHelper.CornerRadius="8"
        AutomationProperties.Name="New chat" ToolTip="New chat (Ctrl+N)">
  <Grid>
    <StackPanel Orientation="Horizontal" HorizontalAlignment="Left">
      <controls:IconView Geometry="{StaticResource Geo_Plus}"
                         Stroke="{DynamicResource TextPrimaryBrush}"
                         Width="15" Height="15" Margin="0,0,7,0" />
      <TextBlock Text="New chat" VerticalAlignment="Center"
                 Foreground="{DynamicResource TextPrimaryBrush}" />
    </StackPanel>
    <TextBlock Text="Ctrl+N" FontSize="10" HorizontalAlignment="Right"
               VerticalAlignment="Center" Opacity="0.5"
               Foreground="{DynamicResource TextSecondaryBrush}" />
  </Grid>
</Button>
```
(Uses the default outlined Button template; drop `Style=AccentButton`.)

### Fix 3 — Empty state: add centered composer + chip row + owl logo — **P1**
**Web:** the composer lives inside the empty state, chips are a horizontal
full-pill row below it, logo is the owl PNG.
**Desktop now:** no composer in empty state; 2×2 chip cards; blue "S".
**Change:**
- Bundle `apps/web-chat/public/sran-logo.png` as `Assets/sran-logo.png`
  (`Resource`); show it 80–96px in the empty state, with a theme-driven invert
  in dark (WPF: use two PNGs, or an `Image` with an
  `<Image.Effect>`-free approach — simplest is to ship a white version and a
  dark version and swap by theme, or keep the "S" tile if the asset can't ship).
- Convert the 2×2 chip grid to a **horizontal WrapPanel of full-pill chips**:
```xml
<WrapPanel HorizontalAlignment="Center">
  <WrapPanel.Resources>
    <Style TargetType="Button" BasedOn="{StaticResource {x:Type Button}}">
      <Setter Property="Margin" Value="4"/>
      <Setter Property="Padding" Value="16,8"/>
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="BorderBrush" Value="{DynamicResource BorderBrush}"/>
      <Setter Property="controls:ButtonHelper.CornerRadius" Value="18"/>
      <Setter Property="Foreground" Value="{DynamicResource TextSecondaryBrush}"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Command" Value="{Binding SuggestionCommand}"/>
    </Style>
  </WrapPanel.Resources>
  <Button CommandParameter="Help me draft ">
    <StackPanel Orientation="Horizontal">
      <controls:IconView Geometry="{StaticResource Geo_Edit}" Width="15" Height="15"
                         Stroke="{DynamicResource TextTertiaryBrush}" Margin="0,0,8,0"/>
      <TextBlock Text="Write" VerticalAlignment="Center"/>
    </StackPanel>
  </Button>
  <!-- Learn / Code / Analyze identical -->
</WrapPanel>
```
- Greeting is already 30px equivalent (`FontSize=24` → bump to **30**); subtitle
  → `FontSize=18`.
- (Optional, larger) render a real composer in the empty state. Lower priority —
  desktop's "select/new chat then type" flow is acceptable; the chip row +
  greeting sizing is the visible win.

### Fix 4 — Theme-aware code blocks — **P1**
**Web:** light theme code = bg `#f7f7f8`, text `#1f1f1f`, header `#eee`; dark =
bg `#1e1e1e`, text `#d4d4d4`, header `#2d2d2d`.
**Desktop now:** `CodeBg #1A1A1A` fixed, `CodeText #E2E8F0` fixed (dark in both).
**Change:** make the code tokens theme-scoped. In `Dark.xaml`:
```xml
<Color x:Key="CodeBgColor">#1E1E1E</Color>
<SolidColorBrush x:Key="CodeTextBrush" Color="#D4D4D4" />
<SolidColorBrush x:Key="CodeHeaderBgBrush" Color="#2D2D2D" />
```
In `Light.xaml`:
```xml
<Color x:Key="CodeBgColor">#F7F7F8</Color>
<SolidColorBrush x:Key="CodeTextBrush" Color="#1F1F1F" />
<SolidColorBrush x:Key="CodeHeaderBgBrush" Color="#EEEEEE" />
```
`MarkdownRenderer.RenderCodeBlock`: set the header background to
`CodeHeaderBgBrush` (currently none), and the language label / copy-icon stroke to
`TextSecondaryBrush` (already theme-aware via `SetResourceReference`).

### Fix 5 — `sidebar-item-active` dark `#2a2a2a` + drop the accent bar — **P2**
**Web:** selected item = `#2a2a2a` (dark) / `#e5e5e5` (light) background, **no**
left accent bar.
**Desktop now:** `BgSelected #3A3A3A` + a 3px blue `AccentBar`.
**Change (`SidebarView.xaml` item template):** add a dedicated
`SidebarActiveBrush` (`#2A2A2A` dark / `#E5E5E5` light) for the selected setter,
and remove the `AccentBar` border (or keep it but tint it neutral). This also
removes another blue accent.

### Fix 6 — Assistant prose 16px / line-height 1.75 — **P2**
**Web:** assistant body 16px, lh 28px. **Desktop:** MarkdownRenderer paragraph
default ≈14px.
**Change (`MarkdownRenderer`):** paragraphs/list text `FontSize=16`,
`LineHeight=28`; keep headings scaled up from there.

### Fix 7 — Assistant action row: always-visible when complete — **P3**
**Web:** copy/👍/👎/regenerate shown always (opacity 1) once streaming ends.
**Desktop:** hover-gated. **Change (`MessageBubble.xaml`):** for assistant, drop
the `Root.IsMouseOver` condition on the action-row MultiDataTrigger; keep only
`IsStreaming=False`. (Keep user actions hover-gated, matching web.)

### Fix 8 — Header: drop centered title, add Memory/kebab (optional) — **P3**
Match web by removing the centered conversation title in the content header and
adding a right-aligned overflow (`⋯`) with Export / Rename / Delete. Lower
priority; the title is harmless.

### Fix 9 — Sidebar extras — **P3**
Add a **New folder** row under New chat, a **collapse chevron** in the sidebar
header (in addition to the content-area toggle), a **token/credit counter** pill
at the bottom-right of the chat area (bind to `/usage/me` if wired), and a
**disclaimer** line ("SRAN Chat can make mistakes. Check important info.", 11px,
text-tertiary) under the composer.

---

## 6. Agentic feature specs (Phase 4)

The web chat app has no desktop-tool "Agent Mode" (it's browser-sandboxed), so
these are **desktop-native** designs that extend the existing agent stack
(`AgentOrchestrator`, `ToolConsentDialog`, agent panel) while matching web's
visual grammar (neutral palette, Lucide icons, pill/rounded surfaces).

### 4.1 Agent Mode panel — show on toggle, richer rows
- On `IsAgentMode = true`, show the panel **immediately** (today it appears only
  once `HasAgentActivity`). Header: `🤖 Agent Mode` + a live "N tools available".
- Three sections: **Available tools** (icon + name, from
  `IMcpToolHost.GetRegisteredTools()`), **Permissions** (per-tool
  allow/ask/deny badge from `tool_permissions`), **History** (last executions).
- Each execution row: category icon, `tool_name`, args summary, a **real-time
  progress bar** while running, elapsed ms on completion, expander revealing full
  args + result. VM already tracks `state`/`DurationText`/`ResultPreview`; add an
  indeterminate `ProgressBar` bound to `IsPending`.

### 4.2 Tool consent UX (already built — align styling)
Desktop already has a card `ToolConsentDialog` (category icon, description, args
preview, Deny / Allow Once / Always Allow) — **better than web**, which has none.
Action: restyle its header tile to the neutral accent (Fix 1) and ensure the
danger banner uses `AccentWarningBrush #f59e0b`.

### 4.3 Thinking / reasoning block
Web renders reasoning as a collapsible block with a duration ("Thought for
3.2s"). Desktop has a `Thinking` Expander already. Enhancements:
- Header text switches from **"Thinking…"** (pulsing) while streaming to
  **"Thought for {duration}"** on completion. `MessageViewModel` already has
  reasoning + can compute duration from the analysis_status start/complete.
- Collapsed by default once complete; expandable to view the reasoning stream.

### 4.4 Search / web-results display
When a turn emits `message_sources` (web search), render **result cards**:
```
[favicon/globe]  Title (accent-neutral, semibold)
                 url (text-tertiary, 11px)
                 snippet (text-secondary, 13px, 2-line clamp)
```
Add a `SearchSource` model + `message_sources` SSE handler in `ChatService`
(currently ignored) → a `SourcesPanel` above/below the assistant text.

### 4.5 Image-generation display
On `message_files` with an image mime, render the image **inline** (max 320px,
rounded 12px) with click-to-open (full size via `Process.Start`). `image_generating`
status already flips a placeholder; wire `message_files` (currently ignored) to a
`ConversationFile` image control.

### 4.6 File-upload UX
- **Drag-drop overlay:** on `DragEnter` over the chat, show a dashed-border
  overlay "Drop files to attach". (Desktop supports drop but shows no overlay.)
- File-type **badges** (pdf/xlsx/img icons) on the pending chips (Lucide file
  icons) instead of the generic paperclip.
- Progress ring per file (already have a `ProgressBar`; switch to a small ring).

### 4.7 Keyboard shortcuts panel
`Ctrl+/` opens a modal listing: `Ctrl+N` new chat, `Ctrl+K` search,
`Ctrl+Shift+S` toggle sidebar, `Ctrl+Shift+Space` global show/hide, `Enter` send,
`Shift+Enter` newline, `Esc` stop. New `ShortcutsDialog` + a window-level
`InputBinding`.

### 4.8 Conversation branch navigation
When a user edits a message and regenerates, the server forks a branch (the
web-chat API exposes `branch_index`/`branch_count` on messages). Desktop should
render `‹ 2/3 ›` arrows on a branched message and call the switch-branch endpoint.
Requires: surfacing branch fields on `MessageDto`, a `BranchNav` control, and
`switchBranch` in `ChatEdgeApiClient` (not yet implemented).

---

## 7. Summary & effort

**Visual parity fixes:** 9 (P0×2, P1×2, P2×2, P3×3)
- **P0 (must):** Fix 1 neutral accent, Fix 2 outlined New chat — ~2–3h
- **P1:** Fix 3 empty-state (composer/chips/logo), Fix 4 theme-aware code — ~4–6h
- **P2:** Fix 5 active-item color/no-bar, Fix 6 prose 16/1.75 — ~1–2h
- **P3:** Fix 7 always-on assistant actions, Fix 8 header, Fix 9 sidebar extras — ~3–4h

**Agentic features:** 8 specs
- Already shipped (align only): 4.2 consent, partial 4.1 panel, partial 4.3 thinking
- New build: 4.4 search cards, 4.5 inline images, 4.6 drag overlay/badges,
  4.7 shortcuts modal, 4.8 branch nav — ~2–3 days total

**Grand total to full parity + agentic polish:** ≈ 4–5 dev-days.
Highest ROI: **Fix 1 + Fix 2 + Fix 3** (removes all blue, matches the empty state)
— that single day closes ~80% of the perceived gap.

---

## 8. One-glance token diff to apply first (P0/P1)

| Change | From | To |
|---|---|---|
| Primary accent (buttons/toggle) | AccentBlue `#3b82f6/#60a5fa` | neutral `#0d0d0d/#ececec` |
| New chat button | filled blue | transparent + border, text-primary |
| Sidebar active (dark) | `#3a3a3a` | `#2a2a2a` |
| Code bg (light) | `#1a1a1a` | `#f7f7f8` |
| Code text (light) | `#e2e8f0` | `#1f1f1f` |
| Code header bg | (none) | `#2d2d2d` / `#eee` |
| Assistant prose | ~14px | 16px / lh 28 |
| Empty-state chips | 2×2 cards | horizontal pill row |
| Empty-state logo | blue "S" | SRAN owl PNG (theme-inverted) |
