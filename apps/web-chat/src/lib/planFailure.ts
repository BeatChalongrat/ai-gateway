// FIX131 — what a plan card says when its execution fails, live or after a
// reload.
//
// NIGHT3: pressing "สร้างเอกสาร" on a PDF → Word plan showed the server's raw
// English ("doc: generator failed: script reported failure"), and after a
// reload the card offered "สร้างเอกสาร" again as if nothing had happened.
// chat-edge now answers a failed execute with a closed-set `reason` (and the
// input file it names), and keeps the failure on the plan's message
// (GET /messages → plan_outcomes). The card words the reason in the user's
// language with what to do next, from either source.

import { ApiError } from "@/lib/api";
import type { DictKey } from "@/lib/i18n/dict";
import type { PlanOutcome } from "@/lib/types";

/** The reasons chat-edge sends (service/plan_failure.go). */
export const PLAN_FAIL_REASONS = [
  "source_encrypted",
  "source_scanned",
  "source_damaged",
  "source_unreadable",
  "source_not_found",
  "invalid_plan",
  "generator_failed",
  "generator_timeout",
  "validation_failed",
  "sandbox_unavailable",
  "internal",
] as const;
export type PlanFailReason = (typeof PLAN_FAIL_REASONS)[number];

export interface PlanFailure {
  reason: PlanFailReason;
  /** The user's input file the reason is about, when it names one. */
  sourceFilename?: string;
}

function asReason(r: unknown): PlanFailReason {
  return typeof r === "string" && (PLAN_FAIL_REASONS as readonly string[]).includes(r)
    ? (r as PlanFailReason)
    : "internal";
}

/** The failure an execute call threw. A network error or an older server
 *  (no `reason`) reads as the generic "couldn't create — try again". */
export function planFailureFromError(err: unknown): PlanFailure {
  if (err instanceof ApiError) {
    const d = err.details ?? {};
    const name = typeof d.source_filename === "string" && d.source_filename ? d.source_filename : undefined;
    if (d.reason !== undefined) return { reason: asReason(d.reason), sourceFilename: name };
    if (err.status === 404) return { reason: "source_not_found" };
    if (err.status === 503) return { reason: "sandbox_unavailable" };
  }
  return { reason: "internal" };
}

/** The failure kept for this card after a reload: the outcome of the same
 *  fence whose output filename is the plan's (both absent counts). */
export function persistedPlanFailure(
  outcomes: PlanOutcome[] | undefined,
  fence: "doc-plan" | "pptx-plan" | "excel-plan",
  outputFilename: string | undefined,
): PlanFailure | undefined {
  if (!outcomes || outcomes.length === 0) return undefined;
  const want = (outputFilename ?? "").trim();
  const hit = outcomes.find(
    (o) => o.fence === fence && o.status === "failed" && (o.output_filename ?? "").trim() === want,
  );
  if (!hit) return undefined;
  return { reason: asReason(hit.reason), sourceFilename: hit.source_filename || undefined };
}

/** The product name of the file type a card makes ({kind} in the text). */
export type PlanFileKind = "Word" | "PDF" | "PowerPoint" | "Excel";

/** The sentence for a failure: the reason, with what the user can do. */
export function planFailureText(
  t: (key: DictKey, vars?: Record<string, string>) => string,
  failure: PlanFailure,
  kind: PlanFileKind,
): string {
  const file = failure.sourceFilename || t("plan.fail.someFile");
  return t(`plan.fail.${failure.reason}` as DictKey, { kind, file });
}
