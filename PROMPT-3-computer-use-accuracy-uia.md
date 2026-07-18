# Task: Computer-use accuracy overhaul + Windows UI Automation for SranDesktop

Work in `SranDesktop/` (WPF .NET 8). Build `dotnet build SranDesktop/SranDesktop.sln`, test `dotnet test`. This is the largest of the parity tasks — do it in the two parts below, in order, keeping the build green between parts.

## Context — current state (verified)

Computer-use tools live in `src/SranDesktop.Infrastructure/Mcp/Tools/` (`ComputerScreenshotTool`, `ComputerClickTool`, `ComputerTypeTool`, `ComputerKeyTool`, `ComputerScrollTool`, `ComputerDragTool`, `OpenApplicationTool`) over one P/Invoke helper `src/SranDesktop.Infrastructure/Native/Win32Input.cs` (`SetCursorPos` + `SendInput`, relative button flags — good, keep). App is PerMonitorV2 DPI-aware (`app.manifest`), so physical pixels are consistent on the primary monitor.

Known accuracy bugs to fix (all verified in code):
1. **Model-side rescaling.** Screenshots wider than 1920 are downscaled and the tool text tells the model to "multiply coordinates by X". If the model forgets, clicks land at a fraction of the target. (`ComputerScreenshotTool.cs` ~lines 59–65, 115–126)
2. **Window screenshots aren't offset-translated.** `computer_screenshot(window_title=…)` returns a window-relative bitmap (PrintWindow) but `computer_click` expects absolute screen coords.
3. **Primary monitor only.** Capture = `CopyFromScreen(0,0,primary)`; click/scroll/drag validate against `SM_CXSCREEN/CYSCREEN` and reject negative coords → secondary monitors unreachable.
4. **No focus guarantee.** `computer_type`/`computer_key` inject into whatever has focus; nothing forces/verifies foreground.

## Part 1 — Coordinate correctness (tool-side, no new tools)

### 1a. Screenshot session state + automatic coordinate mapping

Create `ScreenContext` (Infrastructure, singleton registered in `AppHostBuilder.cs`): stores the LAST screenshot's mapping — scale factor, capture origin in virtual-screen coords (0,0 for full screen; window top-left for window capture), and captured size. `ComputerScreenshotTool` writes it on every capture; `ComputerClickTool` / `ComputerScrollTool` / `ComputerDragTool` read it and transform incoming `x,y` (screenshot space) → screen space: `screen = origin + round(coord * scale)`.

- Change screenshot result text to: `"Screenshot captured: {w}x{h}. Coordinates you see in this image can be used directly with computer_click — do NOT rescale them."` Remove the "multiply by" instruction from the tool text AND update `computer_click`/`computer_scroll`/`computer_drag` descriptions to say coordinates are in screenshot space.
- If a click arrives with no prior screenshot in the session, pass coordinates through unchanged (legacy behavior) — note this in a code comment.
- Keep the mapping per-conversation if trivially possible (the ambient `ShellSession.CurrentConversationId` pattern shows how); otherwise app-global is acceptable (single foreground user).

### 1b. Multi-monitor / virtual desktop

- Full-screen capture: capture the FULL virtual desktop (`SM_XVIRTUALSCREEN`, `SM_YVIRTUALSCREEN`, `SM_CXVIRTUALSCREEN`, `SM_CYVIRTUALSCREEN`) via `CopyFromScreen(virtualX, virtualY, 0, 0, virtualSize)`; `ScreenContext.origin = (virtualX, virtualY)` (can be negative).
- Bounds validation in click/scroll/drag: validate the TRANSFORMED screen coords against the virtual-desktop rect, and allow negative values. Remove the primary-size/negative-reject checks.
- Downscale cap: keep max width 1920 but consider the virtual desktop can be very wide (e.g. 5760) — cap by the LONGER side at 2560 instead if width-only makes multi-monitor text unreadably small; use judgment, document the choice.

### 1c. Window capture offset

For `window_title` captures, record the window's `GetWindowRect` top-left as the origin in `ScreenContext` so clicks on a window screenshot land correctly.

### 1d. Focus before input

- Add optional `window_title` param to `computer_type` and `computer_key`: when provided, focus that window first (reuse `FindWindowByTitle` + `FocusWindow`), wait ~150 ms, and FAIL with a clear error if `GetForegroundWindow()` doesn't match afterwards (use the `AttachThreadInput` + `BringWindowToTop` trick to beat the foreground lock — add to `Win32Input`).
- `computer_click`: after the click, no change. Before the click, if the transformed point is over a window that isn't foreground, that's fine (clicks activate), but do verify `WindowFromPoint` is not the SRAN window itself — refuse clicking our own UI (prevents the agent fighting the host app), with an error telling the model to minimize SRAN first or target another window.

### 1e. `computer_zoom` (new tool)

`computer_zoom(x, y, width, height)` — region in LAST-screenshot coordinates; re-captures that region at FULL native resolution (no downscale) and returns it as an image. Category Screen, DefaultPermission AllowAlways, add to `ToolDangerClassifier.Safe`. Description: "Zoom into a region of the last screenshot at full resolution to read small text or find precise click targets."

### Part 1 tests

Coordinate transform math (screenshot→screen incl. negative origins, scale, window offset), bounds validation, zoom region clamping — as pure `internal static` helpers with xunit tests. No P/Invoke in tests.

## Part 2 — Windows UI Automation layer (element-level control)

Use `System.Windows.Automation` (in-box with WPF/net8.0-windows — add `UIAutomationClient`/`UIAutomationTypes` framework references to the Infrastructure csproj if needed; **no NuGet**). New folder `src/SranDesktop.Infrastructure/Native/Uia/` for the walker/helpers, tools in the usual Tools folder.

### 2a. `get_computer_state` (new tool)

Returns JSON: foreground window (title, process name, bounds), list of visible top-level windows (title, process, minimized?), and whether a modal dialog/popup is on top of the foreground window (UIA `WindowPattern`/element class heuristics). Category Screen, AllowAlways, Safe-listed. Description: "See what applications and windows are open and which is focused. Use before deciding how to act."

### 2b. `read_screen_elements` (new tool)

`read_screen_elements(window_title?)` — walks the UIA tree of the foreground (or named) window and returns a compact indexed list:

```
[3] button "Send reply" enabled bounds=(912,640,80,28)
[4] edit "Search customer" enabled value="" bounds=(120,88,240,24)
[7] checkbox "Remember me" checked=false ...
```

- Include: ControlType, Name, enabled, on-screen bounds (virtual-desktop coords), value/toggle state where cheap. Skip offscreen/zero-size elements. Cap output (e.g. first 400 elements + note) — UIA trees can be huge; prefer `TreeScope.Descendants` with a `Condition` filtering to interactive control types (Button, Edit, ComboBox, CheckBox, RadioButton, ListItem, TreeItem, TabItem, MenuItem, Hyperlink, Document, Text with a name).
- Cache the returned `AutomationElement`s in a per-call snapshot keyed by index (`UiaSnapshot` in ScreenContext or its own singleton), invalidated by the next `read_screen_elements` call.
- Category Screen, AllowAlways, Safe-listed. Description tells the model this is THE preferred way to interact: "Read the interactive elements of a window. Prefer uia_click/uia_set_value with these element ids over pixel clicking — it is far more accurate."

### 2c. `uia_click` (new tool)

`uia_click(element_id, button?=left)` — resolve from the snapshot; verify the element is still alive (`Current` access in try/catch) and enabled; then:
1. Try `InvokePattern`/`TogglePattern`/`SelectionItemPattern`/`ExpandCollapsePattern` as appropriate for the control type.
2. Fallback: focus the element's window, then physical click at the element's clickable point (`GetClickablePoint`, else bounds center) via the existing `Win32Input.Click`.
Return which method was used. Category Screen, DefaultPermission Ask (it acts). If the element is stale, fail with "element list is stale — call read_screen_elements again".

### 2d. `uia_set_value` (new tool)

`uia_set_value(element_id, text)` — try `ValuePattern.SetValue`; fallback: focus element (`SetFocus`), select-all (Ctrl+A) + type via `Win32Input.TypeText`. Category Screen, Ask.

### 2e. Tool description updates for the layered strategy

The computer-use walkthrough system prompt lives gateway-side (NOT in this repo), so the tool descriptions must carry the strategy. Update `computer_screenshot`'s description to: "Take a screenshot of the screen. TIP: for Windows applications, read_screen_elements + uia_click is usually more accurate than looking at pixels — use screenshots when UIA gives poor results (games, custom-drawn apps, Chrome without accessibility) or to visually verify an action's outcome."

### Part 2 notes & edge cases

- Chrome/Electron expose UIA only when accessibility is activated; simply connecting a UIA client usually triggers it, but if `read_screen_elements` on a Chromium window returns almost nothing, return a hint in the result text ("this app exposes limited UI Automation — fall back to screenshot + computer_click").
- Wrap all UIA calls in try/catch (`ElementNotAvailableException`, COM errors) → clean `ToolResult.Fail`, never crash the orchestrator. Run UIA walks on a background thread with a ~5 s timeout (UIA can hang on unresponsive apps).
- Elevated windows will be invisible/unclickable (UIPI) — when a resolve/act fails on an elevated process, say so in the error.
- `ToolDangerClassifier`: `get_computer_state`, `read_screen_elements`, `computer_zoom` → Safe list. `uia_click`, `uia_set_value` stay dangerous (Screen category default).

### Part 2 tests

Element-line formatting, snapshot staleness behavior, output capping — via an abstraction over the UIA element (small interface + fake) so tests need no real UIA. Follow xunit + Moq + FluentAssertions.

## Conventions

- No new NuGet packages (framework references for UIA are fine).
- Register new singletons in `AppHostBuilder.cs`; tools registered alongside the existing computer tools.
- Localization only if you add user-visible UI strings (likely none — these are agent tools).
- Concise `///` comments explaining WHY, matching the codebase style.
- Don't touch: permission/consent flow, `ChatOrchestrator`, `PathGuard`.

## Acceptance

- `dotnet build` clean, `dotnet test` green (new math/formatting tests included).
- Manual (Windows): full-screen screenshot on a >1920px display → ask agent to click a button by looking at the image → click lands exactly (no model-side multiplication). Screenshot of a non-primary monitor window → clickable. `read_screen_elements` on Notepad/Calculator lists buttons; `uia_click` presses one via InvokePattern; `uia_set_value` fills the Notepad edit control. `computer_zoom` returns a crisp full-res crop. Typing with `window_title` focuses the right window or errors clearly.
