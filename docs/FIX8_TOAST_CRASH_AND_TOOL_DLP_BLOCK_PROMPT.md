# FIX 8 — Copy-toast crash + desktop tools falsely blocked by gateway DLP (fresh-session, self-contained)

> **Read this first — you may have no prior context.** This is a monorepo at the repo root containing: a Python/FastAPI + Go **gateway** (the DLP-masking AI gateway, backend at `backend/`, Go edge at `services/gateway/`), **chat-edge-api** (Go, `services/chat-edge-api/`), **mcp-gateway** (Node, `services/mcp-gateway/`), three Next.js apps (`apps/web-chat`, `portal`, `admin-ui`), and a **WPF .NET 8 desktop app** at `SranDesktop/` (Windows-only, an agentic assistant like Claude Desktop). The core product property: **all user content is DLP-masked before it reaches any third-party LLM provider** — do not weaken that. This task fixes two P0 runtime bugs found by clicking the running desktop app.

## Working rules (apply to everything below)
- **Read every file you touch IN FULL before editing.** Do the R0 recon in each bug and answer it in your summary before changing code.
- **Do NOT `git push`.** Commit locally only (the repo's standing rule; the operator pushes). Two independent commits (one per bug) is fine.
- **Do NOT edit source to force a test pass**, do NOT commit secrets or `.env`, do NOT weaken DLP/consent/auth.
- **Runtime is the bar.** This project has repeatedly hit "unit tests green + code looks correct, but it doesn't actually work when clicked" (a prompt looked right but the model ignored it; a Python detector was fixed but the LIVE path was the Rust sidecar; a config secret was wired in one compose file but not another). **A fix is NOT done until proven END-TO-END on the running stack** — each bug below has a mandatory live-proof step. If you cannot run a live proof, mark it UNVERIFIED explicitly; do not claim done.
- Desktop is `net8.0-windows` / WPF — it builds and runs only on Windows. `dotnet build SranDesktop/SranDesktop.sln -c Release` + `dotnet test`. The backend runs via `docker compose` (there's a `Makefile` with `up`/`bootstrap`).
- New user-facing strings must be added to BOTH `En` and `Th` dictionaries in `SranDesktop/src/SranDesktop.Presentation/Localization/Dict.cs` (a `DictParityTests` enforces identical keys) — only if you add strings.

---

## BUG 1 — Copying a message CRASHES the whole app (P0, desktop/WPF)

### Symptom (seen live)
The user copied a chat message; the app threw an unhandled exception and showed the crash dialog:
```
System.InvalidOperationException: Cannot animate the 'Y' property on
'System.Windows.Media.TranslateTransform' because the object is sealed or frozen.
   at System.Windows.Media.Animation.Animatable.BeginAnimation(...)
   at SranDesktop.Presentation.Controls.ToastHost.Card_Loaded(Object sender, RoutedEventArgs e)
      in ...\SranDesktop.Presentation\Controls\ToastHost.xaml.cs:line 127
```

### Root cause (verified in code at HEAD)
`SranDesktop/src/SranDesktop.Presentation/Controls/ToastHost.xaml.cs`, method `Card_Loaded` (~lines 119–128):
```csharp
private void Card_Loaded(object sender, RoutedEventArgs e)
{
    if (sender is not Border card || card.RenderTransform is not TranslateTransform translate)
        return;
    var duration = MotionSettings.Toast;
    var ease = new CubicEase { EasingMode = EasingMode.EaseOut };
    card.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, duration) { EasingFunction = ease });
    translate.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(10, 0, duration) { EasingFunction = ease });
}
```
The `TranslateTransform` comes from the card's `RenderTransform`, which is declared in XAML (a style/template). WPF **freezes** `Freezable` objects that are shared via styles/resources. Calling `BeginAnimation` on a **frozen** `TranslateTransform` throws `InvalidOperationException`. This toast is the Phase-8 toast system; it fires its entrance animation whenever a toast appears — and **copying a message shows a success toast** — so copy → toast → animate frozen transform → crash. (The `Opacity` animation on the card itself doesn't throw because the card `Border` isn't frozen; only the shared transform is.)

### R0 (answer before fixing)
- Read `ToastHost.xaml.cs` (full) and `ToastHost.xaml`: how is the card's `RenderTransform` declared? Is it a shared/styled `TranslateTransform` (hence frozen)? Confirm `Card_Loaded` is the only crash site, and grep the control for every `BeginAnimation` (entrance AND any exit/dismiss animation) that could animate a frozen transform.
- Confirm what `MotionSettings.Toast` returns under reduced-motion (a 0-duration animation still calls `BeginAnimation`, so the frozen-object throw happens regardless of duration).

### Fix
1. Give each toast card its **own unfrozen** `TranslateTransform` to animate, instead of animating whatever (possibly frozen) transform came from the style. The robust pattern: in `Card_Loaded`, assign a fresh instance and animate that:
   ```csharp
   var translate = new TranslateTransform(0, 10);
   card.RenderTransform = translate;              // fresh instance is NOT frozen
   translate.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(10, 0, duration){ EasingFunction = ease });
   ```
   (Or, if you must keep the styled transform: `if (translate.IsFrozen) translate = translate.Clone();` then assign the clone back before animating — `Clone()` returns an unfrozen copy.)
2. **Defensive guard (so a toast can NEVER crash the app again):** wrap the transform animation so that if the transform is still frozen / can't be animated, it degrades to fade-only rather than throwing. A toast is cosmetic; it must never take down the app.
3. Apply the same fix to any exit/dismiss animation and any other `BeginAnimation`-on-transform in the toast control found in R0.

### Live proof (required)
Run the app, **copy a chat message several times** → the success toast slides/fades in with **no crash**. Toggle OS reduced-motion ON → copy again → it snaps in cleanly, still no crash. Report what you observed. (If you can't run the WPF app, mark UNVERIFIED — but this one is easy to hit; a runtime check is expected.)

---

## BUG 2 — Gateway DLP falsely blocks `run_command` (and likely ALL desktop tools) as "unknown_tool" (P0, gateway/backend)

### Symptom (seen live)
The user asked the desktop agent to run `ping -n 10 127.0.0.1` (no PII, no secret, a harmless command). The tool call came back:
```json
{ "_dlp_blocked": true, "reason": "unknown_tool_blocked_masked_args", "tool": "run_command" }
```
→ the UI showed "Blocked by DLP (unknown_tool_blocked_masked_args)" and the command **never ran**. This is not a correct security block — `ping` is harmless — it's the gateway's DLP tool-argument policy not recognizing `run_command` as a known tool.

### Root cause (verified in code at HEAD)
`backend/app/services/dlp/tool_arg_policy.py` — around line 152 it sets `reason = "unknown_tool_blocked_masked_args"`. The policy has a **known-tool allowlist** (`placeholder_safe` / allowed sets, ~L75–152). When a tool call's arguments contain a masked placeholder (DLP replaced something with a `<rN>`-style token) AND the tool is **not in the known-tool list**, the policy blocks the call — the intent being "never execute a tool we can't reason about on content that had PII stripped." But the **desktop's entire tool set was designed AFTER this policy**, so the gateway doesn't know these first-party tools and blocks them:
`run_command`, `read_file`, `write_file`, `edit_file`, `create_directory`, `move_file`, `list_directory`, `search_files`, `run_python`, the computer-use tools (`screenshot`/`click`/`type`/`key`/`scroll`/`drag`/`open_application`), `web_search`, `use_skill`, `update_plan`, `remember`, and remote MCP tools. All are at risk of this false block.

The desktop consumes the `_dlp_blocked` marker in `SranDesktop/src/SranDesktop.Application/Services/ChatOrchestrator.cs` (`IsDlpBlocked`) and correctly refuses to execute a genuinely-blocked call — that behavior is correct and must stay; the bug is the gateway **falsely** marking first-party local tools as blocked.

### R0 (answer before fixing — this is a security-sensitive DLP policy, get it exactly right)
- Read `backend/app/services/dlp/tool_arg_policy.py` IN FULL: the known-tool list / `placeholder_safe` classification (~L75–152), exactly how "masked args" is detected, and WHY the unknown-tool-with-masked-args block exists (the safety property: don't run an unclassifiable tool on masked content, and don't let masked placeholders egress externally as tool args).
- **Why did `ping`'s args get masked at all?** A plain `ping -n 10 127.0.0.1` has nothing to mask. Trace what the DLP masked in that command (is the shell command being over-masked?). Report it — it may be a second, separate over-masking issue.
- List which desktop tools (from the set above) are absent from the known-tool list.
- Confirm the desktop side: `ChatOrchestrator.IsDlpBlocked` (how it reads `_dlp_blocked` and refuses execution) — do NOT weaken this.

### The tension — fix it correctly, don't just disable the guard
The "block unknown tools with masked args" rule is a real safety property (the gateway shouldn't run a tool it can't classify on content whose PII was stripped, nor let masked tokens leak as external tool arguments). **But the desktop tools are first-party and known to us**, and most are **local** — they act on the user's own machine (files, shell, screen) and do NOT send their (possibly masked) arguments to a third-party provider. So the correct fix is to **register/classify the first-party desktop tools in the gateway policy**, not to remove the unknown-tool guard.

### Fix
1. **Classify the first-party desktop tools** in `tool_arg_policy.py`:
   - LOCAL tools that execute on the user's machine and do not egress their args to a provider — `run_command`, `read_file`, `write_file`, `edit_file`, `create_directory`, `move_file`, `list_directory`, `search_files`, `run_python`, the computer-use tools, `update_plan`, `remember` — should be added to the appropriate allow / `placeholder_safe` classification so they are NOT hard-blocked as "unknown." (These already go through the desktop's own consent + `PathGuard` gating client-side; the gateway masking them isn't protecting anything for a local action.)
   - `web_search` is an intentional-raw-egress channel in this system's design — keep its handling consistent with that (don't regress it).
   - Remote MCP tools: decide per R0 — they DO call external servers, so treat them like the existing external-egress classification (a masked-arg MCP call may legitimately need the credential-block behavior). State your decision.
2. **The over-masking of `ping` (if R0 confirms it):** a known local tool with masked args must not be BLOCKED. If the DLP is masking benign shell content, note it as a residual (a second issue) — but the block itself must go for classified local tools.
3. **Keep the unknown-tool guard for genuinely unknown tools** (a tool the gateway has never heard of, with masked args, stays blocked — that safety property is correct and must remain).
4. Do NOT change `ChatOrchestrator.IsDlpBlocked` on the desktop — it must still refuse a genuinely DLP-blocked call.

### Live proof (required)
With the fix deployed to the running gateway: ask the desktop agent `รันคำสั่ง ping -n 4 127.0.0.1 ให้หน่อย` → it **actually runs**, the tool card shows output + exit code, NOT "Blocked by DLP". Also confirm `read_file` / `edit_file` on a real file work end-to-end (they were silently at risk of the same block). Negative checks (safety intact): a genuinely-unknown tool name with masked args is STILL blocked; a tool call whose args contain a real secret is still handled by the existing DLP path (not leaked). Report the evidence. If you can't run the live stack, mark UNVERIFIED.

---

## Final verification checklist
1. **Tests:** backend `pytest` for `tool_arg_policy` (first-party local tools now classified/allowed; genuinely-unknown tool still blocked; secret-carrying call still handled). Desktop `dotnet build` 0/0 + `dotnet test`; a toast-animation test if the frozen-transform decision is extractable to a testable helper (else state it's runtime-only). `DictParityTests` green if strings added.
2. **Live-proof table (the real bar — fill it in honestly):**
   | Bug | Live evidence |
   |---|---|
   | 1 — copy crash | Copy a message ×N → toast animates, no crash; reduced-motion snaps cleanly |
   | 2 — tool block | `ping -n 4 127.0.0.1` runs (output + exit code, not blocked); read_file/edit_file work; unknown-tool+masked still blocked; secret still masked |
   Any row you couldn't run → mark **UNVERIFIED**, do not claim done.
3. **Summary:** R0 answers (both bugs), changes file by file (`ToastHost.xaml.cs` [+ maybe `ToastHost.xaml`]; `tool_arg_policy.py` [+ tests]), whether the `ping` over-masking is a tracked residual, and confirmation that DLP safety (unknown-tool guard, secret handling) is intact. Two local commits, nothing pushed.

These two restore **copy** (a basic action that was crashing the whole app) and the **desktop tool loop against the gateway** (first-party tools were being falsely blocked, so the agent couldn't run commands/edit files reliably).
