"use client";

// A finished run's exceptions (AM2 phase F): shown under the run's outputs
// in the workspace and on the run page. Open rows first — the reason, the
// claim or document, where it was read (sheet row / cell, receipt page,
// the printed text, the table's limit), a policy document's paragraph
// labelled as context only, the proposed action and one button per choice
// with an optional note (POST /exceptions/{id}/decision). A row decided
// elsewhere meanwhile (409 NOT_OPEN) makes the list reload. Decided rows
// say what was chosen, by no one in particular; noted (info) rows are a
// quiet list. There is no reject: only the row's own choices exist.

import { useCallback, useEffect, useState } from "react";
import { CircleCheck, MessageCircleQuestion } from "lucide-react";

import { AmError, amApi, errorMessage } from "../api";
import { useAmLocale, useAmT } from "../i18n";
import { formatDateTime, formatMoney2 } from "../format";
import type { RunExceptionRow } from "../types";
import { BTN_SMALL, Pill, Spin } from "../ui";
import { evidenceParts, groupExceptions, reasonText } from "./exceptions";

/** A run's exceptions, loaded once `enabled` (a finished run). */
export function useRunExceptions(runId: string | null | undefined, enabled = true) {
  const [rows, setRows] = useState<RunExceptionRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!runId || !enabled) return;
    let alive = true;
    amApi.runExceptions(runId).then(
      (r) => {
        if (!alive) return;
        setRows(r?.exceptions ?? []);
        setError(null);
      },
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [runId, enabled, n]);
  const reload = useCallback(() => setN((x) => x + 1), []);
  const replace = useCallback((row: RunExceptionRow) => setRows((cur) => (cur ? cur.map((r) => (r.id === row.id ? row : r)) : cur)), []);
  return { rows, error, reload, replace };
}

/** The panel for one run in the workspace thread: nothing at all when the run left no exceptions. */
export function RunExceptions({ runId }: { runId: string }) {
  const ex = useRunExceptions(runId);
  if (!ex.rows?.length) return null;
  return <ExceptionsPanel rows={ex.rows} onDecided={ex.replace} onStale={ex.reload} />;
}

export function ExceptionsPanel({
  rows,
  onDecided,
  onStale,
}: {
  rows: RunExceptionRow[];
  onDecided: (row: RunExceptionRow) => void;
  /** a row was decided elsewhere: reload the list */
  onStale: () => void;
}) {
  const t = useAmT();
  const g = groupExceptions(rows);
  const [stale, setStale] = useState(false);
  const [showResolved, setShowResolved] = useState(false);
  const [showNoted, setShowNoted] = useState(false);

  return (
    <section className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-exceptions" aria-labelledby="am-exceptions-title">
      <div className="flex flex-wrap items-center gap-2">
        <MessageCircleQuestion width={16} height={16} className="shrink-0 text-text-secondary" aria-hidden />
        <h3 id="am-exceptions-title" className="min-w-0 flex-1 text-sm font-semibold text-text-primary">
          {t("exq.title")}
        </h3>
        <span className="text-xs tabular-nums text-text-secondary" data-testid="am-exceptions-counts">
          {t("exq.counts", { open: String(g.open.length), resolved: String(g.resolved.length) })}
        </span>
      </div>
      <p className="mt-0.5 text-xs text-text-muted">{t("exq.intro")}</p>
      {stale ? (
        <p role="status" className="mt-2 text-xs text-text-secondary" data-testid="am-exceptions-stale">
          {t("exq.stale")}
        </p>
      ) : null}

      {g.open.length ? (
        <ul className="mt-3 flex flex-col gap-2.5">
          {g.open.map((r) => (
            <OpenRow
              key={r.id}
              row={r}
              onDecided={onDecided}
              onStale={() => {
                setStale(true);
                onStale();
              }}
            />
          ))}
        </ul>
      ) : (
        <p className="mt-3 flex items-center gap-2 text-sm text-text-secondary" data-testid="am-exceptions-none-open">
          <CircleCheck width={15} height={15} className="shrink-0 text-status-success" aria-hidden />
          <span>{t("exq.noneOpen")}</span>
        </p>
      )}

      {g.resolved.length ? (
        <div className="mt-3">
          <button
            type="button"
            aria-expanded={showResolved}
            onClick={() => setShowResolved((v) => !v)}
            className="focus-ring rounded-md py-0.5 text-xs text-text-secondary hover:text-text-primary"
            data-testid="am-exceptions-resolved-toggle"
          >
            {t("exq.resolvedToggle", { n: String(g.resolved.length) })}
          </button>
          {showResolved ? (
            <ul className="mt-1.5 flex flex-col gap-1.5">
              {g.resolved.map((r) => (
                <ResolvedRow key={r.id} row={r} />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {g.noted.length ? (
        <div className="mt-2">
          <button
            type="button"
            aria-expanded={showNoted}
            onClick={() => setShowNoted((v) => !v)}
            className="focus-ring rounded-md py-0.5 text-xs text-text-secondary hover:text-text-primary"
            data-testid="am-exceptions-noted-toggle"
          >
            {t("exq.notedToggle", { n: String(g.noted.length) })}
          </button>
          {showNoted ? (
            <ul className="mt-1.5 flex flex-col gap-1 text-xs text-text-secondary" data-testid="am-exceptions-noted">
              {g.noted.map((r) => (
                <li key={r.id} className="min-w-0 break-words" data-testid="am-exception-noted">
                  <span className="text-text-primary">{reasonText(r, t)}</span>
                  {r.source_ref?.claim ? <span>{` · ${r.source_ref.claim}`}</span> : null}
                  {r.proposed_action ? <span>{` · ${r.proposed_action}`}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Evidence({ row }: { row: RunExceptionRow }) {
  const t = useAmT();
  const ref = row.source_ref ?? {};
  const parts = evidenceParts(ref, formatMoney2);
  const kb = (ref.kb_context ?? []).filter((k) => k?.text);
  const also = (ref.also_flagged ?? []).filter((a) => a?.code);
  return (
    <>
      {parts.length ? (
        <p className="mt-1 break-words text-xs text-text-muted" data-testid="am-exception-evidence">
          {parts.map((p) => t(p.key, p.vars)).join(t("common.sep"))}
        </p>
      ) : null}
      {also.length ? (
        <ul className="mt-1 flex flex-col gap-0.5 text-xs text-text-secondary">
          {also.map((a, i) => (
            <li key={`${a.code}-${i}`}>
              {a.detail ? t("exq.also", { reason: a.reason || a.code, detail: a.detail }) : t("exq.alsoShort", { reason: a.reason || a.code })}
            </li>
          ))}
        </ul>
      ) : null}
      {kb.map((k, i) => (
        <blockquote key={i} className="mt-1.5 border-l-2 border-border pl-2.5 text-xs text-text-secondary" data-testid="am-exception-kb">
          <span className="text-text-muted">{t("exq.kb", { file: k.file ?? "", paragraph: String(k.paragraph ?? "") })}</span>
          <span className="block">{`“${k.text}”`}</span>
        </blockquote>
      ))}
    </>
  );
}

function OpenRow({ row, onDecided, onStale }: { row: RunExceptionRow; onDecided: (r: RunExceptionRow) => void; onStale: () => void }) {
  const t = useAmT();
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = row.source_ref ?? {};
  const choices = row.choices ?? [];

  const decide = async (i: number) => {
    const choice = choices[i];
    if (!choice) return;
    setBusy(i);
    setError(null);
    try {
      const res = await amApi.decideException(row.id, note.trim() ? { choice: choice.value, note: note.trim() } : { choice: choice.value });
      onDecided(res.exception);
    } catch (err) {
      setBusy(null);
      if (err instanceof AmError && err.status === 409) {
        onStale();
        return;
      }
      setError(errorMessage(err));
    }
  };

  return (
    <li className="rounded-lg border border-border border-l-status-warning bg-bg-base px-3 py-2.5" style={{ borderLeftWidth: 3 }} data-testid="am-exception" data-id={row.id}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-sm font-medium text-text-primary">{reasonText(row, t)}</span>
        {ref.claim ? <span className="text-xs text-text-secondary">{ref.claim}</span> : null}
      </div>
      {ref.detail ? <p className="mt-0.5 text-xs text-text-secondary">{ref.detail}</p> : null}
      <Evidence row={row} />
      {row.proposed_action ? (
        <p className="mt-1.5 text-sm text-text-primary" data-testid="am-exception-proposed">
          {t("exq.proposed", { action: row.proposed_action })}
        </p>
      ) : null}
      {choices.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5" data-testid="am-exception-choices">
          {choices.map((c, i) => (
            <button
              key={`${c.label}-${i}`}
              type="button"
              disabled={busy !== null}
              onClick={() => void decide(i)}
              className={BTN_SMALL}
              data-testid="am-exception-choice"
            >
              {busy === i ? <Spin size={12} /> : null}
              <span>{c.label}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className="mt-1.5">
        {!noteOpen ? (
          <button
            type="button"
            onClick={() => setNoteOpen(true)}
            disabled={busy !== null}
            className="focus-ring rounded text-xs text-text-secondary underline-offset-2 hover:text-text-primary hover:underline disabled:opacity-50"
            data-testid="am-exception-note-toggle"
          >
            {t("exq.addNote")}
          </button>
        ) : (
          <textarea
            rows={2}
            maxLength={1000}
            value={note}
            disabled={busy !== null}
            onChange={(e) => setNote(e.target.value)}
            aria-label={t("exq.note")}
            placeholder={t("exq.notePlaceholder")}
            className="focus-ring w-full resize-y rounded-md border border-border bg-bg-surface px-2.5 py-1.5 text-xs text-text-primary placeholder:text-text-muted"
            data-testid="am-exception-note"
          />
        )}
      </div>
      {error ? (
        <p role="alert" className="mt-1.5 text-xs text-status-danger">
          {error}
        </p>
      ) : null}
    </li>
  );
}

function ResolvedRow({ row }: { row: RunExceptionRow }) {
  const t = useAmT();
  const locale = useAmLocale();
  const ref = row.source_ref ?? {};
  const decision = ref.decision ?? null;
  return (
    <li className="rounded-lg border border-border-subtle px-3 py-2 text-xs" data-testid="am-exception-resolved">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-text-secondary">{reasonText(row, t)}</span>
        {ref.claim ? <span className="text-text-muted">{ref.claim}</span> : null}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
        <Pill tone="success">{t("exq.decided", { label: decision?.label || "—" })}</Pill>
        {row.resolved_at ? <span className="text-text-muted">{formatDateTime(row.resolved_at, locale)}</span> : null}
      </div>
      {decision?.note ? <p className="mt-1 whitespace-pre-wrap break-words text-text-secondary">{decision.note}</p> : null}
    </li>
  );
}
