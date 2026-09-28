"use client";

// AM3 — "แจ้งปัญหา": one sentence from the person; the run (or the agent),
// the step it stopped at and the page they are on go along by themselves
// (the server captures the run's status and failed step). The answer names
// the reference the admins will see.

import { useState } from "react";
import { MessageSquareWarning } from "lucide-react";

import { amApi, errorMessage } from "../api";
import { AmDialog } from "../dialog";
import { useAmT } from "../i18n";
import { BTN_PRIMARY_SMALL, BTN_SMALL, cls, ErrorPanel, Spin } from "../ui";

export type ProblemTarget = { kind: "run"; runId: string } | { kind: "install"; installId: string } | { kind: "shared"; shareId: string };

export function sendProblem(target: ProblemTarget, text: string, page: string) {
  const body = { text, page };
  if (target.kind === "run") return amApi.runProblem(target.runId, body);
  if (target.kind === "install") return amApi.installProblem(target.installId, body);
  return amApi.sharedProblem(target.shareId, body);
}

function currentPage(): string {
  try {
    return typeof window !== "undefined" ? window.location.pathname : "";
  } catch {
    return "";
  }
}

export function ProblemButton({ target, className, compact = false }: { target: ProblemTarget; className?: string; compact?: boolean }) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cls(BTN_SMALL, className)}
        aria-label={compact ? t("problem.button") : undefined}
        data-testid="am-problem-open"
      >
        <MessageSquareWarning width={14} height={14} aria-hidden />
        <span className={compact ? "hidden sm:inline" : undefined}>{t("problem.button")}</span>
      </button>
      <ProblemDialog target={target} open={open} onClose={() => setOpen(false)} />
    </>
  );
}

/** AM4: the dialog alone — the workspace opens it from its "more" menu. */
export function ProblemDialog({ target, open, onClose }: { target: ProblemTarget; open: boolean; onClose: () => void }) {
  const t = useAmT();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const close = () => {
    onClose();
    if (done) {
      setDone(null);
      setText("");
    }
  };
  const submit = () => {
    if (!text.trim()) {
      setError(t("problem.empty"));
      return;
    }
    setBusy(true);
    setError(null);
    sendProblem(target, text.trim(), currentPage()).then(
      (r) => {
        setBusy(false);
        setDone(r?.message || t("problem.sent", { ref: r?.report?.ref ?? "" }));
      },
      (err) => {
        setBusy(false);
        setError(errorMessage(err));
      },
    );
  };

  return (
    <AmDialog open={open} onClose={close} title={t("problem.title")} description={t("problem.body")} testId="am-problem-dialog">
      {done ? (
        <div className="flex flex-col gap-3">
          <p role="status" className="rounded-lg border border-status-success/40 bg-status-success/10 px-3 py-2 text-sm text-text-primary" data-testid="am-problem-done">
            {done}
          </p>
          <button type="button" onClick={close} className={BTN_SMALL}>
            {t("common.close")}
          </button>
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!busy) submit();
          }}
          className="flex flex-col gap-3"
        >
          <label className="block text-sm">
            <span className="text-text-secondary">{t("problem.label")}</span>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              maxLength={2000}
              placeholder={t("problem.placeholder")}
              className="focus-ring mt-1 w-full resize-y rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
              data-testid="am-problem-text"
            />
          </label>
          <p className="text-[11px] text-text-muted">{t("problem.attached")}</p>
          {error ? <ErrorPanel message={error} /> : null}
          <div className="flex gap-2">
            <button type="submit" disabled={busy} className={BTN_PRIMARY_SMALL} data-testid="am-problem-send">
              {busy ? <Spin size={12} /> : null}
              <span>{t("problem.send")}</span>
            </button>
            <button type="button" onClick={close} disabled={busy} className={BTN_SMALL}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
    </AmDialog>
  );
}
