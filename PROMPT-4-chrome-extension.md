# Task: Chrome extension + browser tools for SranDesktop (Claude-in-Chrome style)

Work in `SranDesktop/`. Build `dotnet build SranDesktop/SranDesktop.sln`, test `dotnet test`. This is the final parity piece: DOM-level browser control through a user-installed Chrome extension.

## Architecture (build exactly this)

```
Agent (ChatOrchestrator) → browser_* tools (Infrastructure)
        ↕ named pipe (JSON lines)          [inside the running WPF app]
SranDesktop.BrowserHost.exe                [tiny relay exe, spawned by Chrome]
        ↕ Chrome native messaging (stdio, 4-byte LE length + UTF-8 JSON)
Chrome Extension (MV3 service worker)
        ↕ chrome.tabs / chrome.scripting
Content script (per tab) — reads DOM, clicks, types
```

Chrome only talks native-messaging to a process IT spawns, so the relay exe is required — it forwards frames 1:1 between stdio and the app's named pipe (`\\.\pipe\sran-browser-{user-sid-hash}`) and exits when either side closes. Keep it dumb: no logic in the relay.

## Part A — C# side

### A1. New project `src/SranDesktop.BrowserHost/`
Console exe, net8.0, no dependencies. Reads Chrome frames from stdin, writes to the pipe; reads pipe, writes Chrome frames to stdout. Frame codec (4-byte little-endian length prefix, 1 MB max message) goes in a small shared class so it's unit-testable. Add the project to the solution and to the publish/installer story (`build/SranDesktop.iss` gets the exe).

### A2. `BrowserBridgeService` (Infrastructure, singleton)
- Owns the named-pipe server (async, single client at a time, reconnect-friendly).
- Request/response correlation: JSON messages `{id, method, params}` → `{id, result}` or `{id, error}`. 30 s default timeout per request; a clean "extension not connected" error when no client.
- **Pairing:** on first run generate a random token (DPAPI-stored). Every message from the extension must carry it; the extension gets it during install (see A4). Reject mismatches.
- Expose `IsConnected` + a status-changed event (Settings UI shows a dot).

### A3. Browser tools (Infrastructure/Mcp/Tools, registered in `AppHostBuilder.cs`)
All Category `Browser`. Every tool fails softly with "Chrome extension not connected — ask the user to install/enable it (Settings → Browser)" when the bridge is down.

| Tool | Params | Default | Behavior |
|---|---|---|---|
| `browser_tabs` | `action`: list \| activate \| new \| close, `tab_id?`, `url?` | Ask (list = AllowAlways — split into `browser_list_tabs` if cleaner) | tab management |
| `browser_navigate` | `url`, `tab_id?` | Ask | navigate active/given tab, wait for load (see A5) |
| `browser_read_page` | `tab_id?`, `filter?`: interactive \| full-text | AllowAlways | indexed element list + page text (see element refs below) |
| `browser_find` | `query`, `tab_id?` | AllowAlways | find elements/text matching a description or selector |
| `browser_click` | `ref`, `tab_id?` | Ask | click element by ref |
| `browser_type` | `ref`, `text`, `clear?`, `submit?` | Ask | focus + set value + input events; `submit` presses Enter |
| `browser_screenshot` | `tab_id?` | AllowAlways | `chrome.tabs.captureVisibleTab` → image result (reuse the ToolResult image path the computer tools use) |

`ToolDangerClassifier`: add `browser_read_page`, `browser_find`, `browser_list_tabs`, `browser_screenshot` to the Safe set. Navigate/click/type stay dangerous (Browser category default).

### A4. Install flow (Settings → new "Browser" section)
- Button "Install Chrome extension": (1) writes the native-messaging host manifest JSON to `%APPDATA%` and registers `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.sran.browser` pointing at it (mirror the `RegisterSranSchemeBestEffort` style in `App.xaml.cs`); manifest's `allowed_origins` = the extension id (make it a build constant; for dev, also accept an id the user pastes); (2) opens the extension's install page or shows load-unpacked instructions pointing at the extension folder.
- Connection status dot + "last seen" text. Localize all strings (Dict.cs, En + Th).

### A5. Waiting for pages
`browser_navigate` and `browser_click` (when navigation results) must wait: `document.readyState === 'complete'` plus a short network-quiet window (e.g. 500 ms with no new requests via `chrome.webRequest` counting, or fallback fixed 800 ms). Return the final URL + title.

## Part B — Extension (`SranDesktop/browser-extension/`, plain JS, no build toolchain)

- `manifest.json` (MV3): permissions `nativeMessaging`, `tabs`, `scripting`, `activeTab`, `webNavigation`, host_permissions `<all_urls>`.
- `background.js` (service worker): owns the `chrome.runtime.connectNative('com.sran.browser')` port, reconnects with backoff, routes methods to tabs, injects `content.js` on demand via `chrome.scripting.executeScript`, includes the pairing token (stored in `chrome.storage.local`, entered once on the extension's options page — options page shows a token field + connection status).
- `content.js`: 
  - **Element refs:** walk the DOM for interactive elements (a, button, input, select, textarea, [role=button|link|textbox|checkbox|combobox], [onclick], [contenteditable]) that are visible (offsetParent/size/opacity checks). Assign stable incremental refs, keep a WeakMap ref→node. Output per element: `[12] <button> "Send reply" (enabled)` plus input values/checked state; include a trimmed page-text section for `full-text`.
  - **Click:** re-verify the node is connected + visible; scroll into view; draw a brief highlight outline (~300 ms) before dispatching real `pointerdown/mousedown/mouseup/click` events at the element center.
  - **Type:** focus, set value via the native setter (React-safe: use `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set`), dispatch `input`/`change`; `submit` dispatches Enter keydown/keyup.
  - Stale refs (page changed) → error "refs are stale — call browser_read_page again".
- Never auto-fill fields whose name/id/type suggests credentials (`password`, `passwd`, `pwd`, `pass`, `secret`, `token`, `api-key`, `apikey`, `otp`, `cvv`) — return an error telling the model the user must type those themselves.

## Part C — small fix rider: credential guard gap

The existing computer-tool credential guard (used by `computer_type` / `uia_set_value`) matches only `password|passwd|pwd`. Extend it to also match `pass`, `secret`, `token`, `api key`/`api-key`/`apikey`, `otp`, `pin` as word-boundary matches (avoid false positives like "passage", "tokenize" — test both directions). Share one implementation between the desktop tools and reuse the same list in the extension's content.js (duplicated constant is fine; add a comment cross-referencing).

## Conventions
- No new NuGet packages. Extension is plain JS/HTML/CSS.
- New singletons in `AppHostBuilder.cs`; interfaces in `Application/Interfaces` (`IBrowserBridgeService`).
- All user-visible strings via `Dict.cs` En + Th.
- Concise `///` WHY-comments matching codebase style.
- **Testing rule (hard): never drive real logged-in sites or real user apps in live smoke tests. Use example.com / a local static page only. Never bypass the consent layer by calling tools directly against real user state.**

## Tests
- Frame codec round-trip + oversize rejection; pipe request/response correlation + timeout + not-connected error; pairing rejection.
- Tool schema/behavior tests with a fake bridge (Moq) — success, stale-ref error passthrough, disconnected message.
- Credential-guard pattern tests (positive + false-positive words).
- Extension JS: manual acceptance only (below) — no JS test toolchain.

## Acceptance
- `dotnet build` clean, `dotnet test` green.
- Manual: load unpacked extension → options page: paste token from Settings → status dot green in app. Then in chat: "เปิด example.com อ่านหน้าแล้วคลิกลิงก์ More information" → navigate (consent) → read_page returns indexed elements → click by ref works with visible highlight → agent reports the new page title. `browser_screenshot` returns an image. Kill Chrome → tools return the clean not-connected message.
