# Part C — manual-equivalent tests on the rebuilt stack (captured 2026-09-02, UTC+7)

Driver: `scripts/fix42-probe/web.js` (real Chromium against the production web-chat bundle on :3002; SSE bodies teed inside the page) and `scripts/fix42-probe/desktop.js` (installed e19 over CDP; real `taskkill /F`). Raw JSON + screenshots (cropped to the main pane) in `scripts/fix42-probe/results/`.

Model for every turn: `google/gemini-2.5-flash` via OpenRouter — see §C1 for why not `auto`.

| case | verdict | evidence |
|---|---|---|
| C1a glass — no masking | **PASS** | gateway `request_logs` `req_fe2c7e0230aa4ee296a15504c9060cd7`: `dlp_input_action=allow`, findings `0`, line sent to the provider = the original text; answer: “ลูกค้าสั่งแก้วน้ำ 12 ใบ และแตก 3 ใบระหว่างการจัดส่งครับ Answ…” |
| C1b คุณแก้ว — name only | **PASS** | `req_883017909ab84aff908874e050990fa1`: `dlp_input_action=mask`, findings `2` (thai_name span 0-7 + thai_ner span 3-7 → one token); line sent = `<<PERSON_A>>โทรมาบอกว่าแก้วน้ำที่สั่งไปแตกสองใบ` — แก้วน้ำ intact |
| C2 real tool turn | **PASS** | live: 11 frames, seq contiguous=True, tool frames [(5, 'code_execution', 'running'), (6, 'code_execution', 'completed')], terminal `message_complete` seq 11 final_seq 11; answer “Ran code 1.8s … 5,736,396” (correct) |
| C2 reattach, fresh tab (cursor 0) | **PASS** | `11` frames, order identical to live incl. `5:code_execution, 6:code_execution` between the text; `same_order_as_live=True` |
| C2 reattach, `starting_after=4` mid-turn | **PASS** | HTTP 200; replay `5:code_execution → 6:code_execution → 7:reasoning_notice → 8:content_delta → 9:content_delta → 10:content_delta → 11:message_complete`; `same_order_as_live=True` — the P1b property against a real tool |
| C3 stop on tab A | **PASS** | A shows “stopped” (True), spinner left False, send button back True |
| C3 tab B sees `cancelled`, not `message_complete` | **PASS** | B's resume stream ended with `cancelled` seq 8 final_seq 8 partial=True; `message_complete` seen: False; B shows “stopped” |
| C3 `/cancel` again ×2 | **PASS** | 200 `{"cancelled":false}` / 200 |
| C4 kill mid-turn → ledger file exists | **PASS** | run 2: killed at 380 rendered chars; `run-ledger/ef3b33df-1885-4c93-b977-3bd18a899694.jsonl` 10 frames, no terminal; app processes gone: True |
| C4 relaunch → partial answer kept, closed with a real status, no spinner | **PASS (after the FIX42 C4 fix)** | shell.log `RUN INTERRUPTED … 11 frame(s) recovered, closing it` → page replays 12 events; bubble: partial answer + “This turn was interrupted when the app closed…” + `AI service error`; composer back to “Message SRAN Chat…” · **run 1 (before the fix)**: same bubble, but composer stuck on “Streaming response…”/Stop for >4 min (`C4-run1-before-fix/`) |
| C4 relaunch → the **question** is kept | **FAIL** | no user bubble; `chat.conversations.message_count=0`, `messages_masked=0` for both C4 conversations; title stays “New conversation”. The ledger holds assistant frames only and `importVisible`/`saveLocalHistory` run after `runLoop`. → OPEN_DECISIONS #41 |
| C4 ledger auto-removed after 10 min | **PASS** | mtime set to 2026-09-01T22:22:56.742Z (11 min back) → relaunch → files 1 → 0 |

## Not the DLP, not FIX41 — live provider state found on the way

- `auto` routed C1 to `deepseek/deepseek-r1` (task=summarize_document) although the request carried 11 tools → gateway `validation_error: Model 'deepseek/deepseek-r1' does not support tool/function calling` → the page shows “Model unavailable”. Router is off-limits this round; recorded in FIX42_REPORT §5. (`C1-attempt1-*.json`)
- `openai` is skipped by the router (`auto_routing_skip_unhealthy`) because `gpt-4o-mini` returns `rate_limit_exceeded` — the OpenAI account, not code. Title generation falls back to `openrouter/auto`.
- `openai/gpt-4o-mini` via OpenRouter → `404 no endpoint for the requested model` on this key today (worked once on 08-31). (`C1-attempt2-*.json`)
