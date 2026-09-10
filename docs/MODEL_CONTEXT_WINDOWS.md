# How big each model actually is

FIX101 Part B. Measured 2026-09-10 against the running deployment.

Before this, nothing in the product had ever asked a model how much it
holds. `model_configs.max_context_tokens` was seeded — every self-hosted
model got the same blanket `SELF_HOSTED_DEFAULT_CONTEXT_TOKENS = 32768`
(`backend/app/services/discovery/provider_discovery.py`) — and the
context compiler did not read even that, budgeting instead against its own
hard-coded map with a 120,000 fallback.

**The catalogue is not evidence.** It said Typhoon held 32,768. The
provider refuses it at 16,384. Four of the seven self-hosted models were
overstated by 2x and one was understated by 4x; only two were right.

## Method

One oversized request per model through this repo's own gateway with
`GATEWAY_TEST_API_KEY`, `stream=true`, `max_tokens=16`, and a body far
larger than any plausible window. An oversized request is refused before
inference, so it costs nothing and returns in seconds — and the refusal
sentence names the cap, which is the strongest evidence available short of
provider documentation.

    POST /v1/chat/completions   ~200,000 chars (~44,550 input tokens)
    -> context_length_exceeded, details.provider_message names the limit

A model that ACCEPTS the probe has only given a lower bound; escalate
until it refuses. qwen3.8-27b-fp8 accepted 44,550 and had to be re-probed
at ~138,000 before it would say what it was.

`max_tokens` counts against the window: qwen refused at "131,057 input
tokens ... you requested 16 output tokens ... total of at least 131,073"
against a 131,072 cap. So the window is TOTAL, which is why the budget
subtracts an output reserve from it rather than adding one on top.

## What the models actually hold

| Model | Catalogue said | Real | Off by | Evidence |
| --- | ---: | ---: | --- | --- |
| `Typhoon-S-ThaiLLM-8B-Instruct` | 32,768 | **16,384** | 2x over | provider refusal, twice on 2026-09-10 — once in the owner's own incident at 16,413 input tokens, once here |
| `OpenThaiGPT-ThaiLLM-8B-Instruct-v7.2` | 32,768 | **16,384** | 2x over | provider refusal |
| `Pathumma-ThaiLLM-qwen3-8b-think-3.0.0` | 32,768 | **16,384** | 2x over | provider refusal |
| `THaLLE-0.2-ThaiLLM-8B-fa` | 32,768 | **16,384** | 2x over | provider refusal |
| `qwen3.5-9b` | 32,768 | **32,768** | correct | provider refusal |
| `qwen3.6-35b-a3b` | 32,768 | **32,768** | correct | provider refusal |
| `qwen3.8-27b-fp8` | 32,768 | **131,072** | 4x under | accepted 44,550; refused at 131,057 with the cap named |

`qwen3.8-27b-fp8` is the owner's default model and nobody had ever tested
it. It is the largest window on the box by 4x, and the app was treating it
as the same 32,768 as an 8B.

Verbatim, the two that matter:

```
This model's maximum context length is 16384 tokens. However, your request
has 44550 input tokens. Please reduce the length of the input messages.
```

```
litellm.ContextWindowExceededError: ... This model's maximum context length
is 131072 tokens. However, you requested 16 output tokens and your prompt
contains at least 131057 input tokens, for a total of at least 131073
tokens. ... model=qwen3.8-27b-fp8
```

## Where the numbers now live

Both places, deliberately:

* **The catalogue** (`model_configs.max_context_tokens`), written through
  the admin console's own `PATCH /admin/models/{id}`, which sets
  `is_manual_override=true` so the discovery sync stops re-guessing them.
  This is the authority the compiler reads.
* **`ModelContextLimits`** in `context_budget.go`, as a seed, so a
  gateway-down window budgets these correctly instead of falling to the
  conservative default. Seed values are WORKING limits (headroom already
  applied); catalogue values are ADVERTISED (headroom applied at read).

## Not measured, and why

* **Vendor models.** Their catalogue values come from published
  documentation. The one known discrepancy is `deepseek-v4-pro`, catalogued
  at 128,000 against a documented 1M — understated, therefore safe, and
  correcting it is an admin edit rather than a measurement.
* **Learning the limit from a refusal automatically.** Deliberately out of
  scope: no vendor was found doing it, and it should not ship in the same
  round that makes trimming fire for the first time.

## Re-running this

The probe is one `curl` per model. Re-run it when a model is added to the
box, when the box is re-served with different flags, or when a
`context_length_exceeded` appears for a model whose catalogue number says
it should have fitted.

## The desktop's budget as the app runs

FIX105. FIX104's desktop figures came from a harness whose tool service listed
no tools. Measured again on 2026-09-11:

* The installed app's own `SranDesktop.ToolsSidecar.exe` was running.
* The owner's app was closed first, because the tool service claims a
  per-user browser pipe.
* Numbers come from the desktop host's own `context.info` dry run.

| | Typhoon-S-ThaiLLM-8B-Instruct (16,384) | qwen3.8-27b-fp8 (131,072) |
| --- | ---: | ---: |
| Working limit | 14,500 | 117,500 |
| Reply reserve (the `max_tokens` 8,192 sent, or chat-edge's quarter, whichever is larger) | 8,192 | 16,000 |
| System prompt (estimated) | 1,855 | 1,855 |
| Tools offered | 0: tools-blind | 29: 21 from the tool service, 8 host-side |
| Tool reserve budgeted (200 each) | 0 | 5,800 |
| Tool definitions actually serialized | none | 19,434 chars ≈ 4,868 tokens |
| **History budget** | **4,453** | **93,845** |
| Same, with the tool service stubbed out (FIX104's harness) | 4,453 | 98,045 |
| The web's history budget, same model | 3,944 | 94,578 |
| Messages actually sent on a trimmed conversation | 4 of 8 (2,252 tokens) | 54 of 138 (55,958 tokens) |

**Typhoon: nothing changes.** It cannot use tools, so the desktop sends it
none whether the tool service runs or not. The estimate "14,500 − system −
~6,000 tools − 8,192 → the 2,000 floor" does not apply.

* Its history budget is 4,453 tokens: two to four Thai messages of the length
  these probes used.
* The 8,192-token reply reserve is the largest single item in that budget.
* Whether the desktop should ask a 16k model for fewer output tokens is a
  design decision, not made here.

**qwen3.8: the tool service costs 4,200 tokens of history budget.**

* The desktop sits 733 tokens *below* the web's budget, not 3,666 above it as
  FIX104's table implied.
* The 200-per-tool reserve covers the definitions actually sent: an estimated
  4,868 tokens against 5,800 reserved.

**FIX104's parity conclusions.**

* Still true: the meter states, the Thai wording and the wall.
* No longer true: the "first trimmed turn" comparison on qwen3.8 (web 3,
  desktop 8). Part of that gap was the stubbed tool service. Most of it was
  the desktop loading only the newest 100 messages of a web conversation,
  which FIX105 Part B removed.
