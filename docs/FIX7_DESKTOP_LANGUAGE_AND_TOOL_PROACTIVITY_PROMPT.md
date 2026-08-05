# FIX 7 — Desktop replies in the user's language + is proactive with tools (feels like web-chat)

Two runtime UX gaps seen live in SranDesktop that make it feel a step below web-chat/ChatGPT/Claude — the kind users complain about immediately:

1. **Language mismatch (the P0):** the user typed in Thai ("ไฟล์ในโฟลเดอร์ Desktop\agent-test มีอะไรบ้าง อ่าน notes.txt ให้หน่อย") and the assistant replied in **English** ("In the folder Desktop\agent-test, there are the following files…"). Web-chat replies in Thai. ChatGPT/Claude always answer in the language the user wrote. This is table-stakes and it's broken on desktop.
2. **Not proactive with tools:** asked to "run `ping -n 10 …`", the assistant **described the command in a code block instead of running it** (it did use tools correctly on the next message — list_directory/read_file worked). So tool-calling works, but the model sometimes explains instead of acting.

Both are almost certainly the **desktop agent system prompt** lacking instructions that web-chat's path has (and possibly amplified by the `gpt-4o-mini` default model). Desktop-only (the agent system prompt). Do NOT `git push`, do NOT edit to force a test pass.

## R0 — Reconnaissance (answer before editing)
1. **Desktop agent system prompt:** find where it's built — `ChatViewModel.BuildGatewayMessages` (the base `system` message, ~L1521-1571 in earlier audits) + `BuildSystemExtrasAsync` (the per-turn `## …` sections). Read the FULL current system prompt text. Confirm: does it contain ANY "respond in the user's language" instruction? (Almost certainly not — that's the bug.) Does it tell the model to USE tools proactively / act rather than describe?
2. **What web-chat does differently:** does web-chat (or the gateway) inject a language-matching instruction or a different system prompt that makes it answer in Thai? Check `apps/web-chat/src/lib/api.ts` (does it send a system prompt?), and the gateway's `inject_dlp_system_prompt` (`orchestrator.py:664`) — is there a shared prompt the desktop is missing? Report whether web-chat's Thai behavior comes from a prompt instruction, the default model, or both.
3. **Model default:** confirm the desktop's default model (the picker showed `gpt-4o-mini`). Note that gpt-4o-mini is weaker at language-follow + proactive tool use than larger models — so the prompt fix matters MORE here (the instruction must be explicit).

## Fix

### Part A — Respond in the user's language (the must-fix)
Add an explicit instruction to the desktop agent system prompt (in `BuildGatewayMessages`'s base system message, so it's always present and never trimmed):

> "Always respond in the SAME language the user writes in. If the user writes in Thai, respond entirely in Thai (including explanations around tool results and code comments where natural). Only use another language if the user explicitly asks."

- Make it prominent (near the top of the system prompt, not buried after tool docs).
- Keep it natural — the model should still show code/commands verbatim (those aren't "translated"), but its prose, explanations, and summaries follow the user's language.
- Don't hardcode Thai — it's "match the user," so an English user still gets English. (Optionally seed the app's current UI locale as a hint, but the user's actual message language wins.)

### Part B — Be proactive: act with tools, don't just describe
Add an instruction that pushes the model to USE its tools to accomplish the task rather than explaining how the user could do it:

> "You have tools to act on the user's computer (run commands, read/write/edit files, etc.). When the user asks you to DO something you have a tool for, USE the tool and do it — don't just describe the command or tell the user to run it themselves. Only describe instead of acting if the action is destructive/ambiguous and needs confirmation."

- This addresses the ping case (it explained instead of running). Consent gating still applies — proactive ≠ bypassing consent; the model calls the tool, and the existing PermissionService prompts if needed.
- Balance: don't make it recklessly run things — the "destructive/ambiguous → confirm" clause + the existing consent flow keep it safe.

### Part C — (investigate, optional) default model
If R0 shows gpt-4o-mini is materially worse at following these instructions than the other catalog models, consider whether the desktop's DEFAULT should be a stronger model (or note it as a recommendation). Don't force a model change if gpt-4o-mini follows the new prompt acceptably — verify after Part A/B.

## Verify (runtime is the bar — we learned this)
This is a prompt change; unit tests can pin that the instruction STRING is present in the built prompt, but the real proof is behavioral:
1. **Unit:** `BuildGatewayMessages` output contains the language + proactivity instructions (in the never-trimmed system message). `DictParityTests` green if any localized strings added.
2. **Live proof (required):** with the app running, ask in Thai ("ไฟล์ในโฟลเดอร์นี้มีอะไรบ้าง") → the reply is **in Thai**. Ask "รันคำสั่ง ping -n 4 127.0.0.1 ให้หน่อย" → it **actually runs** `run_command` (tool card appears), doesn't just describe it. Test with the default `gpt-4o-mini` (the real-world case) AND note if a stronger model behaves better. If you can't run live, mark UNVERIFIED — do not claim done (this is exactly the class of "prompt looks right but model ignores it" that only live-testing catches).
3. Confirm no regression: English user still gets English; tool consent still prompts; DLP unchanged.

## Report
- R0: the current system prompt (does it lack language/proactivity instructions?) + what web-chat does differently.
- The added instructions (exact text).
- **Live proof:** Thai-in → Thai-out; "run ping" → actually runs (tool card). On gpt-4o-mini specifically. Screenshots or the tool-call evidence.
- Whether a stronger default model is recommended.
- File-by-file changes (should be small — the system prompt builder). Not pushed.
