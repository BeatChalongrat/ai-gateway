# Summary recall probe

FIX106 Part B. It answers one question: can this probe tell two summary
prompts apart?

FIX102's recall check could not: both summaries lost the topic numbers, so it
returned the same score whatever changed.

## What it measures

`spec.json` was written before either arm ran. It holds:

- a 24-message Thai conversation with seven checkable facts spread across it:
  - a number
  - a code name
  - an item of a numbered list, by its number
  - an item of a second numbered list
  - a decision
  - a corrected value
  - the user's original request and its reason
- one standing rule the user gives on turn 2: end every answer with `K7-OK`
- the questions, the expected answers, the checks and both thresholds

For each arm and each of two runs, `probe.js`:

1. **Folds the whole conversation into a summary with that arm's prompt.**
   - Arm A is the extraction prompt at `2b0a265`.
   - Arm B is the FIX106 form. It was the prompt in the tree when the probe
     ran. It did not ship, so it now lives in `form-b.txt`.
   - Both use the shipped request body and renderer, which chat-edge and the
     desktop share through `testdata/state_contract.json`.
2. **Asks each question with only a system prompt, the summary pair and the
   question.** No conversation message is sent, so an answer can only come
   from the summary. The probe checks this for every request rather than
   assuming it.
3. **Checks each answer and records the gateway's `cached` flag.**
   - Every request streams.
   - Each run carries its own tag, so no request repeats across arms or runs.

## Running it

    npm --prefix apps/desktop run build
    node scripts/probes/summary-recall/probe.js

Requirements:

- the local stack running
- `GATEWAY_TEST_API_KEY` in `.env` (it is read and never printed)
- `qwen3.8-27b-fp8` answering

The run takes 32 calls. It writes `results.json` beside this file, including
every summary, every answer and the verdict.

If a run cannot produce a summary, or the model refuses or times out, that run
does not count. With fewer than two counted runs per arm, the verdict is
"ยังสรุปไม่ได้".

## Result, 2026-09-10 (`results.json`)

32 calls to `qwen3.8-27b-fp8`, all streamed. No response carried a `cached`
flag, so the cache hit count is 0. The streaming path never consults the
gateway response cache.

Every question request sent 0 conversation messages.

| Run | Parsed | Recall (checker) | Rule K7-OK | Summary pair | Share of 3,944 |
|-----|--------|------------------|------------|--------------|----------------|
| A1  | yes    | 7/7              | 7/7        | 740 tokens   | 18.8 %         |
| B1  | yes    | 4/7              | 7/7        | 663 tokens   | 16.8 %         |
| A2  | yes    | 7/7              | 7/7        | 660 tokens   | 16.7 %         |
| B2  | **no** | —                | —          | —            | —              |

**Verdict under the rule written first: "ยังสรุปไม่ได้".** Arm B has one
counted run, not two, so the form does not ship. The evidence in hand points
the same way.

- **B2's JSON did not parse.** The form asks for verbatim lists, and the model
  wrote list items as bare strings inside `structured_summary`. In the product,
  a summary that does not parse is a fold with no summary at all.
- **B1 lost facts.** Its `details` section copied one line verbatim, the code
  name, and left out the server count and both numbered lists. Q1 said the
  detail was lost, as the honesty note tells it to. Q3 invented a step 3
  ("Cutover").
- **The checker's errors cancel out on B1.** Q6 answered correctly (week 5,
  moved from week 3) but failed the pre-written check, which also requires
  "เดิม". Q4 passed because it says "DNS", but it named lowering the TTL as
  risk 2 rather than DNS still pointing at the old system. Read by hand, B1 is
  still 4/7.
- **A needs no rescue.** Arm A recalled 7/7 in both runs, so a 2-question
  lead for B was impossible. The probe can tell two prompts apart, which
  FIX102's could not, and here it told them apart in A's favour.
