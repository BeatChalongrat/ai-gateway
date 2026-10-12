"use client";

// A run's inline progress card. The runner rebuilds its lines from the step
// rows, so the card always says what the audit trail says. Elapsed time
// ticks locally while the run is working (from started_at). While the round
// works, the card's title is the app's own "working" indicator (AM5: the
// chat's CatIndicator), not a spinner of its own.
//
// AM6 — the title and the colour go by what the run came to: a round that
// delivered its draft while the agent's gate did not pass is finished, with
// a note on the gate's line, and is not drawn as a failure.

import { useEffect, useState } from "react";
import { Check, Pause, X } from "lucide-react";

import { CatIndicator } from "@/components/chat/CatIndicator";
import { Button, ButtonLink } from "@/components/ui/Button";

import { amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import { elapsedBetween, formatDuration } from "../format";
import { MODEL_UNREACHABLE, outcomeKey, outcomeOf } from "../outcome";
import type { ProgressPayload, ProgressStepStatus } from "../types";
import { cls } from "../ui";
import { runHref } from "./context";
import { aboutTime } from "./Estimate";
import { personText } from "./personText";

const STEP_STATUS: Record<string, AmKey> = {
  done: "stepStatus.done",
  running: "stepStatus.running",
  waiting: "stepStatus.waiting",
  failed: "stepStatus.failed",
  pending: "stepStatus.pending",
  skipped: "stepStatus.skipped",
  open: "stepStatus.open",
};

const REASON: Record<string, AmKey> = {
  superseded_by_new_round: "reason.superseded_by_new_round",
  cancelled_by_user: "reason.cancelled_by_user",
};

const ACTIVE = new Set(["queued", "running", "awaiting_input"]);

export function StepIcon({ status }: { status: ProgressStepStatus | string }) {
  const box = "inline-flex h-4 w-4 shrink-0 items-center justify-center";
  switch (status) {
    case "done":
      return (
        <span className={cls(box, "text-status-success")} aria-hidden>
          <Check width={15} height={15} strokeWidth={2.5} />
        </span>
      );
    case "running":
      return (
        <span className={box} aria-hidden>
          <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
        </span>
      );
    case "waiting":
      return (
        <span className={cls(box, "text-status-warning")} aria-hidden>
          <Pause width={14} height={14} strokeWidth={2.5} />
        </span>
      );
    case "failed":
      return (
        <span className={cls(box, "text-status-danger")} aria-hidden>
          <X width={15} height={15} strokeWidth={2.5} />
        </span>
      );
    case "open":
      // AM6: the step finished and left a note — a check, in the colour of a note
      return (
        <span className={cls(box, "text-status-warning")} aria-hidden data-step-icon="open">
          <Check width={15} height={15} strokeWidth={2.5} />
        </span>
      );
    case "skipped":
      return (
        <span className={cls(box, "text-text-muted")} aria-hidden>
          <span className="h-px w-2.5 bg-current" />
        </span>
      );
    default:
      return (
        <span className={cls(box, "text-text-muted")} aria-hidden>
          <span className="h-2 w-2 rounded-full border border-current" />
        </span>
      );
  }
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

export function ProgressCard({
  progress,
  hideRunLink = false,
  expectedSeconds,
}: {
  progress: ProgressPayload;
  hideRunLink?: boolean;
  /** AM10: how long a round takes here; shown while this one runs. */
  expectedSeconds?: number | null;
}) {
  const t = useAmT();
  const status = progress.status;
  const outcome = outcomeOf(progress);
  const title = t(outcomeKey(outcome, progress.reason) ?? "progress.title.running");
  const active = ACTIVE.has(status);
  const ticking = status === "running" && !!progress.started_at;
  const now = useNow(ticking);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const elapsed = ticking
    ? elapsedBetween(progress.started_at, null, now)
    : progress.elapsed_ms || elapsedBetween(progress.started_at, progress.finished_at);
  const steps = progress.steps ?? [];
  const reason = progress.reason ? REASON[progress.reason] : undefined;
  // AM10: the time, at the moment of waiting — and, once it has run past
  // one and a half times that, that it is still working.
  const expected = status === "running" && progress.kind !== "self_test" ? aboutTime(t, expectedSeconds) : null;
  const longer = !!expected && !!expectedSeconds && elapsed > expectedSeconds * 1.5 * 1000;

  const cancel = () => {
    setCancelling(true);
    setError(null);
    amApi.cancelRun(progress.run_id).then(
      () => setCancelling(false),
      (err) => {
        setCancelling(false);
        setError(errorMessage(err));
      },
    );
  };

  return (
    <div
      className={cls(
        "rounded-xl border bg-bg-surface px-4 py-3",
        outcome === "failed" ? "border-status-danger/40" : status === "awaiting_input" ? "border-status-warning/40" : "border-border",
      )}
      data-testid="am-progress"
      data-status={status}
      data-outcome={outcome}
      data-run={progress.run_id}
    >
      <div className="flex items-center gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-text-primary" data-testid="am-progress-title">
            {active && status !== "awaiting_input" ? <CatIndicator label={title} /> : title}
          </div>
          {progress.kind === "self_test" ? <div className="text-xs text-text-muted">{t("progress.selfTest")}</div> : null}
          {expected ? (
            <div className="text-xs text-text-muted" data-testid="am-progress-expected" data-longer={longer || undefined}>
              {longer ? t("progress.longer", { time: expected }) : t("progress.expected", { time: expected })}
            </div>
          ) : null}
        </div>
        <div className="flex-1" />
        {elapsed > 0 ? (
          <div className="shrink-0 text-xs tabular-nums text-text-muted" data-testid="am-progress-elapsed">
            {formatDuration(elapsed, t)}
          </div>
        ) : null}
      </div>

      {steps.length ? (
        <ol className="mt-2.5 flex flex-col gap-1.5">
          {steps.map((s) => (
            <li key={s.key} className="flex items-start gap-2.5 text-sm" data-step-status={s.status}>
              <span className="mt-0.5">
                <StepIcon status={s.status} />
              </span>
              <span className={cls("min-w-0 flex-1", s.status === "running" ? "text-text-primary" : s.status === "pending" ? "text-text-muted" : "text-text-secondary")}>
                <span>{personText(s.label) || t(STEP_STATUS[s.status] ?? "stepStatus.pending")}</span>
                {personText(s.detail ?? "") ? <span className="text-text-muted">{` · ${personText(s.detail ?? "")}`}</span> : null}
                <span className="sr-only">{` (${t(STEP_STATUS[s.status] ?? "stepStatus.pending")})`}</span>
              </span>
            </li>
          ))}
        </ol>
      ) : null}

      {reason ? <p className="mt-2 text-xs text-text-secondary">{t(reason)}</p> : null}
      {status === "awaiting_input" && progress.awaiting_ms ? (
        <p className="mt-2 text-xs text-text-muted">
          {/* AM15: a run that waits for its model has waited for the model, not for an answer of the person's */}
          {t(progress.reason === MODEL_UNREACHABLE ? "progress.waitedModel" : "progress.waited", { d: formatDuration(progress.awaiting_ms, t) })}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 text-xs text-status-danger">
          {error}
        </p>
      ) : null}

      {!hideRunLink || active ? (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-border-subtle pt-3">
          {!hideRunLink ? (
            <ButtonLink href={runHref(progress.run_id)} variant="secondary" size="sm">
              {t("progress.viewAll")}
            </ButtonLink>
          ) : null}
          {active ? (
            <Button type="button" variant="secondary" size="sm" onClick={cancel} loading={cancelling} data-testid="am-progress-cancel">
              <span>{cancelling ? t("progress.cancelling") : t("progress.cancel")}</span>
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
