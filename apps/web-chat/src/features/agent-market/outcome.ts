// AM6 — what a run came to, as a person reads it.
//
// The database keeps the statuses it always had: a round whose gate did not
// pass is "failed" there, which is what refuses a sign-off. What a person is
// told goes by the outcome: such a round delivered its work and was charged
// for it, so it reads "เสร็จแล้ว · ยังไม่ลงตัว" in the colour of a finished round
// with a note, and never as a failure. A server older than AM6 sends no
// outcome; the status then says what it always said.

import type { AmKey } from "./i18n";
import type { Tone } from "./ui";

export const COMPLETED = "completed";
export const COMPLETED_OPEN = "completed_open";
/** The gate's code of a reconciliation whose two balances differ. */
export const NOT_TIED = "not_tied";

export function outcomeOf(run: { outcome?: string | null; status?: string | null }): string {
  if (run.outcome) return run.outcome;
  return run.status === "succeeded" ? COMPLETED : run.status ?? "";
}

/** The round delivered its work (and is charged for it). */
export function delivered(outcome: string): boolean {
  return outcome === COMPLETED || outcome === COMPLETED_OPEN;
}

const KEYS: Record<string, AmKey> = {
  queued: "progress.title.queued",
  running: "progress.title.running",
  awaiting_input: "progress.title.awaiting_input",
  completed: "progress.title.succeeded",
  succeeded: "progress.title.succeeded",
  failed: "progress.title.failed",
  cancelled: "progress.title.cancelled",
};

/** AM15: the reason of a run that waits for a model it may use to be reachable again. */
export const MODEL_UNREACHABLE = "model_unreachable";
/** AM24: the head of the reason of a run parked until its budget is raised (runner/selfserve.go parkForBudget). */
export const BUDGET = "budget_";

/** The words for an outcome; `reason` is the run's reason code (never shown itself). */
export function outcomeKey(outcome: string, reason?: string | null): AmKey | null {
  if (outcome === COMPLETED_OPEN) return reason === NOT_TIED ? "outcome.open.notTied" : "outcome.open.gate";
  // a run that waits for its model asks nothing of the person: it is not "waiting for your answer"
  if (outcome === "awaiting_input" && reason === MODEL_UNREACHABLE) return "progress.title.waiting_model";
  // AM24: nor one that waits for budget (budget_install, budget_org, …): an admin raises it, nobody answers it
  if (outcome === "awaiting_input" && reason?.startsWith(BUDGET)) return "progress.title.waiting_budget";
  return KEYS[outcome] ?? null;
}

const TONES: Record<string, Tone> = {
  completed: "success",
  succeeded: "success",
  completed_open: "warning",
  failed: "danger",
  awaiting_input: "warning",
};

export function outcomeTone(outcome: string): Tone {
  return TONES[outcome] ?? "neutral";
}
