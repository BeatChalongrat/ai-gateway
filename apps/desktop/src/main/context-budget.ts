// FIX104 — how big a conversation may get before the desktop trims it.
//
// Until this round the desktop typed its own answer: a six-entry map
// (`auto` and five gpt ids at 120,000) with 100,000 for everything else and
// a fixed 16,000 output reserve. FIX101 moved chat-edge onto the catalogue
// and found that Typhoon's real window is 16,384; the desktop never learned
// it. A Typhoon conversation on the desktop was budgeted for ~70,000 tokens
// of history, so it never trimmed: it hit the provider's refusal first
// ("8192 > 16384 - 12961", measured in FIX104) and every later turn failed
// the same way.
//
// Now the WINDOW comes from the gateway's catalogue (`context_window` on
// GET /v1/models — model_configs.max_context_tokens, the number an operator
// edits), and the RULES that turn a window into a budget are chat-edge's,
// ported here. They are not copies anybody maintains: context-budget-pin
// .test.ts reads the constants out of chat-edge's Go source and fails when
// either side changes alone.
//
// One deliberate difference, and it is the reason this file does not simply
// re-export chat-edge's numbers: chat-edge sends no max_tokens, so its output
// reserve is a heuristic. The desktop sends max_tokens on every turn, and a
// provider counts it against the window (vLLM refuses when prompt +
// max_tokens > window). The desktop's reserve is therefore never smaller
// than what it actually asks for — see outputReserveForRequest.
//
// No 'electron' import: unit-tested in plain Node.

/** chat-edge headroomFraction: the working limit is 90 % of the advertised window. */
export const HEADROOM_FRACTION = 0.9;
/** chat-edge headroomStep: rounded down to a clean number. */
export const HEADROOM_STEP = 500;
/** chat-edge smallestObservedWindow: the smallest window measured on a model this deployment serves. */
export const SMALLEST_OBSERVED_WINDOW = 16_384;
/** chat-edge outputReserveFor: a quarter of the working limit, clamped. */
export const OUTPUT_RESERVE_DIVISOR = 4;
export const OUTPUT_RESERVE_MIN = 2_000;
export const OUTPUT_RESERVE_MAX = 16_000;
/** chat-edge perToolTokens: what one tool definition costs. */
export const PER_TOOL_TOKENS = 200;
/** chat-edge CalculateBudgetWith: never less than this for history. */
export const MESSAGES_FLOOR = 2_000;

/** chat-edge workingLimitFrom: an ADVERTISED window → the WORKING limit budgeted against. */
export function workingLimitFrom(advertised: number): number {
  const a = advertised > 0 ? advertised : SMALLEST_OBSERVED_WINDOW;
  let w = Math.trunc(a * HEADROOM_FRACTION);
  if (w >= HEADROOM_STEP) w -= w % HEADROOM_STEP;
  return w;
}

/** chat-edge defaultContextLimit: a model the catalogue does not know (14,500). */
export const DEFAULT_CONTEXT_LIMIT = workingLimitFrom(SMALLEST_OBSERVED_WINDOW);

/** chat-edge outputReserveFor. */
export function outputReserveFor(totalBudget: number): number {
  let reserve = Math.trunc(totalBudget / OUTPUT_RESERVE_DIVISOR);
  if (reserve > OUTPUT_RESERVE_MAX) reserve = OUTPUT_RESERVE_MAX;
  if (reserve < OUTPUT_RESERVE_MIN) reserve = OUTPUT_RESERVE_MIN;
  return reserve;
}

/** The desktop's reserve: chat-edge's rule, but never less than the max_tokens the request carries. */
export function outputReserveForRequest(totalBudget: number, requestedOutputTokens: number): number {
  return Math.max(outputReserveFor(totalBudget), requestedOutputTokens > 0 ? requestedOutputTokens : 0);
}

/** What one turn's budget is measured against. */
export interface BudgetBasis {
  /** The model the window belongs to — for `auto`, the model that answered the last turn; "" when none is known. */
  model: string;
  /** Its ADVERTISED window from the catalogue; 0 when the catalogue does not know it. */
  window: number;
  /** The max_tokens the request will send (0 = none). */
  outputTokens: number;
}

/** The working limit for a basis: the catalogue's window with headroom, or the conservative default. */
export function workingLimitFor(basis: BudgetBasis): number {
  return basis.window > 0 ? workingLimitFrom(basis.window) : DEFAULT_CONTEXT_LIMIT;
}
