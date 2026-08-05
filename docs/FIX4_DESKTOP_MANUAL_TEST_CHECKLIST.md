# FIX 4 — SranDesktop manual test checklist (verify Phase A/B/C at runtime)

This is a **human click-through checklist**, not a code task — the smoke test proved the app builds/launches (728 unit tests green) but could NOT verify the Phase A/B/C behavior at runtime (no GUI automation harness). Run these by hand and record the result. A WPF UI-automation harness is a separate future pass; this gets you the "does it actually work when I click it" answer now.

**How to use:** mark each ✅ works / ⚠️ partial / ❌ broken / ⏭️ couldn't test, and jot the actual behavior + anything that felt rough (a stutter, a wrong color, a missing state) — the polish details matter as much as pass/fail.

## Prerequisites (do first)
- [ ] Backend stack up and healthy (gateway :8080, chat-edge :4000, portal). Recall the two deploy requirements just fixed: **`SERVICE_JWT_SECRET` matches on gateway + chat-edge** (FIX 1) and **`OPENAI_API_KEY` set on the gateway** (FIX 2), and schema is at head (FIX 3 will fail-loud if not).
- [ ] For MCP/connectors: `docker compose --profile desktop up` (mcp-gateway :3200).
- [ ] For code interpreter: `make sandbox-up` (`SANDBOX_ENABLED=1`).
- [ ] Build + launch: `dotnet build SranDesktop/SranDesktop.sln -c Release`, run the app.
- [ ] Have a throwaway test folder with a few files (e.g. `%USERPROFILE%\Desktop\agent-test\` with a `.txt`, a `.cs` or `.py`, a small `.csv`).

---

## Section 0 — Startup & auth
- [ ] First-run dialog appears; point it at the local gateway/chat-edge URLs.
- [ ] Log in with the seeded user (`somchai@acme.ai.gateway` / `P@ssword` — note: `uat-seed-output.txt` records a stale password; use `P@ssword`).
- [ ] Agent key self-provisions silently (no key prompt shown to you — that's the point).
- [ ] Send a plain "hello" → a reply streams in token-by-token (no freeze, smooth).

## Section 1 — DLP masking is visible end-to-end (the core product)
- [ ] Send: `My name is John Smith, email john@acme.com, phone 555-123-4567, and my key is sk-test-abc123`.
- [ ] The reply comes back coherent (the model understood you).
- [ ] In the admin UI (:3000) chat observability OR the trace, confirm what egressed to the provider was **masked** (`<<PERSON_A>>`, `<<EMAIL_1>>`, etc.) and the secret was withheld — i.e. the desktop's content went through the same masking as web-chat. (This is the differentiator vs Claude Desktop; worth seeing it live.)

## Section 2 — Phase A: agentic core (the "works like Claude Desktop" claims)
- [ ] **Read/list**: "List the files in `[agent-test path]` and tell me what's in the .txt" → tool-call cards appear (`list_directory`, `read_file`), execute, results shown.
- [ ] **Consent**: the first write/command asks for permission; note the dialog is **themed** (not a native Windows MessageBox) and shows a **danger banner** for the risky action (FIX from Phase C.3).
- [ ] **edit_file (Phase A)**: "In `[test .cs/.py file]`, change the function name X to Y" → confirm it makes a **targeted edit** (only that line changes — open the file and check), NOT a full-file rewrite.
- [ ] **Folder-scoped consent (Phase A.4)**: ask it to edit 3 files in the test folder after clicking "Always allow" once → it should prompt **once**, not three times.
- [ ] **Plan/todo (Phase A.5)**: give a multi-step task ("read all files, summarize each, write a summary.md") → a **checklist** renders and items check off as it works.
- [ ] **Durable history (Phase A.1)**: do a 2–3 step tool task, then **close and reopen the conversation** → the tool steps are still there (not just the final text). Then ask a follow-up that relies on what it did earlier → it remembers.
- [ ] **Resume on cap (Phase A/6)**: (hard to force — optional) a very long task that hits the step budget shows a **"Continue"** affordance rather than a dead "stopped" error.

## Section 3 — Phase B: agentic depth
- [ ] **Streaming shell (B.1)**: "run `[a command that takes >30s]` — e.g. a loop or a real build" → output appears **live in a mini-terminal**, line by line, not all-at-once-at-the-end, and it doesn't die at 30s.
- [ ] **Working dir persists (B/PART3-A shell)**: `cd` into the test folder in one command, then run another command → it's still in that folder (cwd retained).
- [ ] **Parallel sub-agents (B.2)**: "analyze these 3 files in parallel: A, B, C" → multiple sub-agent child-cards appear under the parent and progress **concurrently**; a combined per-file result comes back.
- [ ] **remember (B.3)**: "remember that this project builds with `dotnet build X.sln`" → later in the SAME conversation (or after reopen) it recalls it without you repeating. Check the note shows up in context.

## Section 4 — Phase C: polish
- [ ] **Inline image (C.1)**: ask it to make a chart (via code interpreter) or send a message with a `![](data:image/png;base64,…)` → the image **renders inline**, not as an icon+link.
- [ ] **Jump-to-latest (C.2)**: during a long stream, scroll UP → a floating **↓ button** appears; click it → smooth-scrolls to newest and re-pins. (Turn on OS reduced-motion → it snaps instead of animating.)
- [ ] **Model picker keyboard nav (C.4)**: open the model picker, use **Up/Down/Enter/Esc** → navigates by keyboard, and each model shows a **description** (provider · context), not just a name.
- [ ] **System theme (C.5)**: set theme to **System/Auto**, then change Windows light↔dark → the app **re-themes live** without restart.
- [ ] **Themed confirm (C.5)**: delete a conversation/folder → a **themed ConfirmDialog** (not native MessageBox); errors show as **toasts**.

## Section 5 — Office / real work value (the "helps office work" claims)
- [ ] **Local Office gen**: "make an xlsx of [some data]" / "draft a Word report about X" / "make a 3-slide pptx" → produces a real **.xlsx/.docx/.pptx** you can open (Phase 12/16 local generation). Confirm the file opens in Excel/Word/PowerPoint and looks right.
- [ ] **Data analysis (code interpreter, needs sandbox-up)**: upload the test `.csv` → "analyze this and chart it" → runs Python, returns stdout + an inline chart.
- [ ] **Computer use**: "take a screenshot and tell me what's on screen" → captures, the vision reply describes it. (Optional: "open Notepad and type hello" → controls the machine, with consent.)

## Section 6 — Connectors / MCP (needs --profile desktop)
- [ ] Connector marketplace loads (note if catalog URLs are placeholders / "configure your URL").
- [ ] If a local stdio MCP server is available, connect it → its tools appear and can be called (through the consent gate).

---

## Report back
For each section: the ✅/⚠️/❌ marks + a one-line note on what actually happened and any rough edge (stutter, wrong theme color, missing loading state, a state that felt off). Flag anything that's **worse than ChatGPT/Claude/Gemini** in feel even if it technically "works" — those polish gaps are the next round. Also note anything that needed config you didn't have (no OpenAI key, sandbox off, no MCP server) as ⏭️ rather than ❌.

This checklist is the runtime counterpart to the 728 unit tests — together they tell us what genuinely works when a real user clicks, which the code audits alone couldn't.
