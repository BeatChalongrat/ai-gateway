"use client";

// Phase 8 Task 2 — sandboxed run_python card.
//
// Before this, a python run was invisible: the user saw the typing dots
// for up to 60s and then a file appeared. Now each run renders as one
// card carrying the code the model wrote, its stdout/stderr, and the
// run state — the same "code + output" affordance ChatGPT shows.
//
// One card per tool call, keyed by the backend's tool_call id: the
// `running` frame creates it, the terminal frame fills in the output in
// place (see the code_execution handler in chatStore).
//
// Auto-expanded while running (that is when the user wants to see what
// is happening); collapses to a one-line summary shortly after it ends,
// same rhythm as ThinkingBlock. Produced files are NOT listed as
// downloads here — they already arrive as normal message attachments.

import { useEffect, useState } from "react";

import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  SquareTerminal,
  TriangleAlert,
} from "lucide-react";

import { useT } from "@/lib/i18n";
import type { CodeRun } from "@/lib/types";

/** How long the finished card stays open before folding away. Matches
 *  ThinkingBlock's 1s beat so the two don't fight for attention. */
const COLLAPSE_DELAY_MS = 1000;

export function CodeRunCard({ run }: { run: CodeRun }) {
  const t = useT();
  const running = run.status === "running";
  const [expanded, setExpanded] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (running) {
      // A re-run (or a reattach that replays `running`) must re-open
      // the card even if the user had collapsed the previous state.
      setExpanded(true);
      return;
    }
    const timer = window.setTimeout(() => setExpanded(false), COLLAPSE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [running]);

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(run.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard denied (permissions / insecure context) — the code is
      // selectable on screen, so a failed copy is not worth an error UI.
    }
  }

  const failed = run.status === "error";
  const duration =
    !running && run.duration_ms ? (run.duration_ms / 1000).toFixed(1) : null;

  return (
    <div
      className="mb-2 overflow-hidden rounded-lg border border-border"
      data-testid="code-run-card"
      data-status={run.status}
    >
      <div className="flex items-center gap-2 bg-bg-secondary px-3 py-1.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex flex-1 items-center gap-1.5 text-left text-xs text-text-muted transition-colors hover:text-text-secondary"
          aria-expanded={expanded}
        >
          {failed ? (
            <TriangleAlert className="h-3.5 w-3.5 text-status-danger" aria-hidden />
          ) : (
            <SquareTerminal className="h-3.5 w-3.5" aria-hidden />
          )}
          {running ? (
            <span className="shimmer-text text-xs font-medium">
              {t("coderun.running")}
            </span>
          ) : (
            <span className="flex items-center gap-1.5">
              <span>{failed ? t("coderun.failed") : t("coderun.ran")}</span>
              {duration ? (
                <span className="text-text-muted">
                  {t("coderun.duration", { seconds: duration })}
                </span>
              ) : null}
              {expanded ? (
                <ChevronDown className="h-3 w-3" aria-hidden />
              ) : (
                <ChevronRight className="h-3 w-3" aria-hidden />
              )}
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={copyCode}
          aria-label={t("code.copyAria")}
          className="text-text-muted transition-colors hover:text-text-secondary"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Copy className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </div>

      {expanded ? (
        <div>
          <pre className="max-h-[320px] overflow-auto bg-bg-base px-3 py-2 text-xs leading-relaxed text-text-primary">
            <code>{run.code}</code>
          </pre>
          <OutputPane run={run} />
        </div>
      ) : null}
    </div>
  );
}

/** stdout / stderr / timeout, in the order the user reads them. Nothing
 *  renders while the job is still running — an empty black box would
 *  read as "it produced nothing". */
function OutputPane({ run }: { run: CodeRun }) {
  const t = useT();
  if (run.status === "running") return null;

  const stdout = run.stdout ?? "";
  const stderr = run.stderr ?? "";
  const nothing = !stdout && !stderr && !run.timed_out;

  return (
    <div className="border-t border-border px-3 py-2 text-xs">
      <p className="mb-1 font-medium text-text-muted">{t("coderun.output")}</p>
      {run.timed_out ? (
        <p className="mb-1 text-status-danger">{t("coderun.timedOut")}</p>
      ) : null}
      {stdout ? (
        <pre className="max-h-[240px] overflow-auto whitespace-pre-wrap break-words text-text-secondary">
          {stdout}
        </pre>
      ) : null}
      {stderr ? (
        <pre className="mt-1 max-h-[240px] overflow-auto whitespace-pre-wrap break-words text-status-danger">
          {stderr}
        </pre>
      ) : null}
      {nothing ? <p className="text-text-muted">{t("coderun.noOutput")}</p> : null}
      {run.truncated ? (
        <p className="mt-1 text-text-muted">{t("coderun.truncated")}</p>
      ) : null}
    </div>
  );
}
