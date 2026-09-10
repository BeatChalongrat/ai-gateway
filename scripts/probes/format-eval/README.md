# format-eval — does a prompt change actually make the answer better?

FIX99. The first harness in this repo that can tell whether editing the system
prompt helped. Fifty rounds shipped prompt changes on judgement; this measures
one instead, and it is built to be reused for the next section rather than
thrown away.

It is deliberately narrow. **Formatting is scored by counting, not by
opinion** — that is why it was chosen to go first. A later section (wellbeing,
evenhandedness, handling mistakes) will need judgement, and this harness does
not provide it; see "What this cannot do" below.

## The method, in the order it must happen

1. **Pre-register** (`prereg.json`): the asks, a label for each, the rule the
   labels were assigned by, and the thresholds that decide the verdict. All of
   it written before a single call. A threshold picked after seeing the
   numbers is not a threshold.
2. **Run both arms** (`runarms.py`): same asks, same order, one arm per prompt
   variant.
3. **Score** (`score.py`) and **compare** (`analyse.py`) against the
   pre-registered thresholds.
4. **Obey the verdict**, including when it says the change lost.

## Three rules the harness enforces, each bought with a wasted round

- **Stream every request.** FIX95's harness sent `stream=false`, measured a
  path the product does not use for chat, and its wrong conclusion cost two
  rounds. FIX98 proved streaming succeeds where non-streaming dies at our own
  30 s read timeout.
- **Never repeat `(model, messages)`.** The response cache keys on
  `{"model", "messages"}` sha256'd (`backend/app/services/cache_service.py`),
  so an identical re-run is a cache hit by construction and measures Redis.
  Here the question differs per ask and the system prompt differs per arm, so
  no two calls in a run share a key. `cached` is recorded on every response and
  the hit count is reported.
- **Score mechanically.** `score.py` has a fixed precedence — code if more than
  40 % of characters are fenced, else table, else numbered, else bullets, else
  prose. `bullets` is not a valid label, so an answer that bullet-points an
  explanation always scores a miss. That is the behaviour under test.

## Running it

    python runarms.py <scratchdir> <model> <runs>      # writes arms-<model>.jsonl
    python analyse.py <scratchdir> arms-<model>.jsonl  # the table

`<scratchdir>` holds `key.txt` (GATEWAY_TEST_API_KEY), `arm-before.txt` and
`arm-after.txt` (the two compiled system prompts). Nothing here contains a key.

## What this cannot do

- **Judgement.** Format is countable; "did it handle the mistake gracefully" is
  not. A section needing judgement needs a rubric and a grader — most likely a
  stronger model scoring blind against pre-registered criteria, with a sample
  hand-checked. That is a bigger build and it is not here.
- **Survive an unhealthy box.** FIX99's own run died at 93 of 120 calls when
  the self-hosted model began terminating streams; 18 rows survived, which is
  under one observation per ask per arm and decides nothing. A future run
  should health-check between asks and abort early rather than spend an hour
  discovering it at the end.
- **Tell you why.** It reports that a number moved, not the reason.

## The first verdict (FIX99)

**ยังสรุปไม่ได้** — the candidate section in `candidate-section.txt` was NOT
shipped. The run collected 18 usable rows of the 120 planned before the
inference box degraded, which cannot support the pre-registered 15-point
threshold. The partial numbers are recorded in the FIX99 report and are
directionally interesting, including a Thai-ratio drop in the direction the
round was most worried about — at an n where a single answer moves it by whole
points. The section is kept here as a candidate, unshipped, for the re-run.
