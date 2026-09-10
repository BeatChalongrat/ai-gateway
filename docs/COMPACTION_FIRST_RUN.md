# Watching compaction work, for the first time

FIX101 Part C. Run 2026-09-10 against the running dev stack.

Every line of the trimming path had shipped and none of it had ever
executed. All 35 compiler snapshots ever recorded from the owner's
conversation say `compacted=false`, `dropped=0`, `state_injected=false`,
and the meter's own function returns hidden for all 35 — because the
budget those ratios were measured against did not exist (see Part A).

This is what happens now that it does.

## How the run was driven

web-chat's browser talks to chat-edge over `POST /conversations`,
`POST /conversations/{id}/messages` (SSE) and
`GET /conversations/{id}/context-info` with a portal JWT. A test account
was created — `fix101-context-probe@gbtech.co.th`, its own tenant, its own
credits — and those exact endpoints were walked. The compiler, the budget,
the meter numbers, the extraction and the trim are therefore the real ones.
What is skipped is React rendering, and only that.

**Not done: the pixels.** The browser extension was not connected, so the
meter was never photographed. Everything below marked "what the user sees"
is the data the components consume plus the string they format it with,
read out of `ContextMeter.tsx` and `dict.ts` — not a screenshot. Astra
could not run this experiment at all because `GATEWAY_TEST_API_KEY` cannot
drive the authenticated web path; the account fixes that half, not this
one.

The owner's account, credits and conversations were not touched.

## Small window — Typhoon-S-ThaiLLM-8B-Instruct (16,384 real)

Budget: total 14,500 · output reserve 3,625 · system prefix 4,731 · tool
reserve 2,200 · **messages 3,944** · trims at **3,352**.

| Turn | history | ratio | meter | trimmed | dropped | sent/total | summary injected |
| ---: | ---: | ---: | :-: | :-: | ---: | :-: | :-: |
| 1 | 810 | 20.5% | no | no | 0 | 2/2 | – |
| 2 | 1,588 | 40.3% | no | no | 0 | 4/4 | – |
| 3 | 2,325 | 59.0% | no | no | 0 | 6/6 | – |
| 4 | 3,071 | 77.9% | **appears** | no | 0 | 8/8 | – |
| 5 | 3,825 | 97.0% | yes | **fires** | 6 | 6/10 | **yes** |
| 6 | 4,584 | 116.2% | yes | yes | 8 | 6/12 | yes |
| 12 | 9,034 | 229.1% | yes | yes | 21 | 5/24 | yes |

The compiler's own log line for a real send at turn 12:

```
status=compacted budget_total=14500 messages_budget=3944 threshold_tokens=3352
messages_total=21 messages_sent=5 dropped=18 head_kept=2 history_tokens=2433
compacted=true state_injected=true extraction_triggered=true dry_run=false
```

`state_injected=true` is the line that has never been true before.

**Does the conversation survive it?** Turns 6-12 all completed with real
replies (299-390 characters each, read back out of the database). Then, with
22 of 24 messages dropped from the sent history:

* "what was topic 1?" → correct. This proves the **head-preserving trim**,
  not the summary: `head_kept=2` means the first exchange is sent verbatim.
* "what were topics 4 and 6?" → topic 4 **correct**, topic 6 **wrong** (it
  answered topic 5's subject). Both are in the saved summary, in order.
  This is the 8B model misreading its own 8B-written summary, not the
  pipeline losing them.
* It was told "say so if you don't remember, don't guess" and it guessed.

## Large window — qwen3.8-27b-fp8 (131,072 real)

Budget: total 117,500 · output reserve 16,000 · system prefix 4,723 · tool
reserve 2,200 · **messages 94,577** · trims at **80,390**.

Driven with ~12,000-character messages to reach the threshold in a
sensible number of turns.

| Turn | history | ratio | meter | trimmed |
| ---: | ---: | ---: | :-: | :-: |
| 1 | 7,999 | 8.5% | no | no |
| 8 | 63,954 | 67.6% | no | no |
| 9 | 71,920 | 76.0% | **appears** | no |
| 10 | 79,802 | 84.4% | yes | no |
| 11 | 87,681 | 92.7% | yes | **fires** |
| 22 | 175,334 | 185.4% | yes | yes |

Real sends at the end, from the compiler log:

```
status=compacted budget_total=117500 messages_budget=94577 threshold_tokens=80390
messages_total=43 messages_sent=16 dropped=29 head_kept=2 history_tokens=56552
compacted=true state_injected=true extraction_triggered=true dry_run=false
```

A 175,000-token history trimmed to 56,552 sent, on a model whose real
window is 131,072 — it fits, and it would not have without this.

## What the user sees

From the meter's own code and dictionary, at the values measured above:

* **Nothing at all** below 70%. `CONTEXT_METER_SHOW_RATIO = 0.7`, and no
  consumer chat app shows a permanent context meter.
* From 70%: `Context 78% used · ~2 messages left` /
  `ใช้บริบทไป 78% · เหลืออีก ~2 ข้อความ`, with a
  `9,269 / 3,944 tokens` tooltip and a **Summarize and continue** /
  **สรุปแล้วไปต่อ** button.
* Once a summary exists: `· Older messages summarized` /
  `· สรุปข้อความเก่าแล้ว` appended.
* Under the reply, a source row:
  `Conversation compacted (22 older messages summarized)`.

## What did not work — reported, not fixed

1. **The meter pins at 100% and its copy goes wrong.** `used_ratio` is the
   FULL loaded history over the messages budget, so once trimming is in
   steady state it is permanently >1 (measured 229% and 185%), the meter
   shows a capped "Context 100% used", and the copy switches to "will
   summarize older messages next" — on every turn, while the app is in fact
   summarizing on every turn already. The number a person reads stops
   tracking anything they can act on the moment the feature starts working.
2. **The compaction source row is English only.** It is built in Go
   (`compactionMarkerLabel`, `context_compiler.go`) rather than from the
   dictionary, so it says "Conversation compacted (22 older messages
   summarized)" on a Thai screen. The meter around it is translated; this
   one line is not, and the i18n ratchet cannot see it because it is not in
   the web tree.
3. **The summary costs a third of a small model's budget.** Typhoon's
   summary measured 1,029 tokens against a 3,944-token message budget. It
   is injected as a user/assistant pair at the head, so on a 16,384-token
   model roughly a quarter of everything sent is the summary.
4. **A summary the model cannot afford is skipped, and the trim proceeds
   anyway.** When the test account ran out of credits mid-run, extraction
   failed with `insufficient_credits` and the log read
   `state coverage incomplete before trim` — then it trimmed regardless.
   That is the documented design ("extraction can fail and still proceed")
   and the context note does say no summary is available, but the effect is
   that older messages leave the conversation with nothing standing in for
   them, at the exact moment the account is in trouble.
5. **The 8B model misreads its own summary** (topic 6 above) and guesses
   when told not to. Choosing a stronger model to write the summary is
   FIX102 and was deliberately not attempted here.
6. **`extraction_triggered` reads false in `/context-info`** while the
   compiler logs it true for the same conversation. `/context-info` is a
   `dry_run=true` compile, which by design does not extract — so the field
   reports the dry run's own behaviour, not the conversation's. Anyone
   reading the endpoint to find out whether extraction happens would
   conclude it never does. That is how the "35 snapshots, all false" figure
   should be read, too.

## Turns before the first trim, in practice

Calibrated on the owner's own conversation `e493ca28`: ~11,025 history
tokens across ~25 messages is ~440 tokens per message, ~880 per turn. With
his 11 tools attached and his ~4,730-token system prefix:

| Model | Real window | Messages budget | Trims at | **Turns** |
| --- | ---: | ---: | ---: | ---: |
| Typhoon / OpenThaiGPT / Pathumma / THaLLE | 16,384 | 3,944 | 3,352 | **~4** |
| qwen3.5-9b / qwen3.6-35b-a3b | 32,768 | 14,819 | 12,596 | **~14** |
| qwen3.8-27b-fp8 | 131,072 | 94,577 | 80,390 | **~91** |
| `auto`, and any model nobody has catalogued | unknown | 3,944 | 3,352 | **~4** |

Measured directly: Typhoon trimmed at turn 5 with ~760-token turns, and
qwen at turn 10-11 with ~8,000-token turns. Both match the table.

Four turns is short, and it is the honest number for an 8B Thai model that
holds 16,384 tokens while 7,000 of them are spent on the system prompt and
tool definitions before the conversation says a word. The way to make it
longer is to send fewer tools and a shorter prefix, or to use a bigger
model — not to raise the ceiling, which is what the app was doing.
