"use client";

// AM4 — the pieces of a round boundary: the divider that closes a finished
// round (its files, and "เริ่มรอบใหม่" one click away), an older round folded
// into that same line, and the chip that asks — never guesses — whether what
// arrived after a finished round starts a new one.

import { useState } from "react";
import { ChevronDown, Download, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { Spinner } from "@/components/ui/Spinner";

import { amApi, errorMessage } from "../api";
import { extensionOf } from "../format";
import { useAmT } from "../i18n";
import { COMPLETED_OPEN, NOT_TIED } from "../outcome";
import type { IntentPayload, OutputArtifact, ThreadItem } from "../types";
import { cls } from "../ui";
import { useWorkspace } from "./context";
import type { RoundInfo } from "./rounds";

function roundLabel(t: ReturnType<typeof useAmT>, round: RoundInfo): string {
  if (round.kind === "self_test") return t("round.selfTest");
  if (round.status === "succeeded") return round.period ? t("round.done", { period: round.period }) : t("round.doneNoPeriod");
  // AM6: a round that delivered its draft with the gate open is finished, with a note.
  // AM11: each outcome has its own sentence, with a period and without — nothing is glued in.
  if (round.outcome === COMPLETED_OPEN) {
    if (round.reason === NOT_TIED) return round.period ? t("round.open.notTied", { period: round.period }) : t("round.open.notTiedNoPeriod");
    return round.period ? t("round.open.gate", { period: round.period }) : t("round.open.gateNoPeriod");
  }
  if (round.status === "cancelled") return t("round.cancelled");
  return round.period ? t("round.failed", { period: round.period }) : t("round.failedNoPeriod");
}

function FileLink({ a }: { a: OutputArtifact }) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        setBusy(true);
        amApi.downloadArtifact(a.id, a.name).then(
          () => setBusy(false),
          () => setBusy(false),
        );
      }}
      title={t("common.downloadNamed", { name: a.name })}
      className="focus-ring inline-flex max-w-[16rem] items-center gap-1.5 rounded-lg border border-border-subtle bg-bg-surface px-2 py-1 text-xs text-text-secondary hover:border-border hover:text-text-primary"
      data-testid="am-round-file"
    >
      <FileTypeBadge extension={extensionOf(a.format)} filename={a.name} size="sm" />
      <span className="truncate">{a.name}</span>
      {busy ? <Spinner size={11} /> : <Download width={12} height={12} aria-hidden className="shrink-0" />}
    </button>
  );
}

/** The line that closes a finished round. The latest one offers the next round. */
export function RoundDivider({ round, latest, started }: { round: RoundInfo; latest: boolean; started: boolean }) {
  const t = useAmT();
  const ws = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = () => {
    if (!ws) return;
    setBusy(true);
    setError(null);
    amApi.startRound(ws.installId, ws.threadId ?? null).then(
      (r) => {
        setBusy(false);
        if (r?.item) ws.merge([r.item]);
        ws.focusComposer?.();
      },
      (err) => {
        setBusy(false);
        setError(`${t("round.startFailed")}: ${errorMessage(err)}`);
      },
    );
  };
  return (
    <div className="flex flex-col gap-2 py-1" data-testid="am-round-divider" data-status={round.status} data-outcome={round.outcome} data-run={round.runId}>
      <div className="flex items-center gap-3" role="separator" aria-label={roundLabel(t, round)}>
        <span className="h-px flex-1 bg-border" aria-hidden />
        <span className="shrink-0 text-xs font-medium text-text-secondary">{roundLabel(t, round)}</span>
        <span className="h-px flex-1 bg-border" aria-hidden />
      </div>
      {round.artifacts.length ? (
        <div className="flex flex-wrap justify-center gap-1.5" aria-label={t("round.files")}>
          {round.artifacts.map((a) => (
            <FileLink key={a.id} a={a} />
          ))}
        </div>
      ) : null}
      {round.notIncluded > 0 ? (
        <p className="text-center text-xs text-status-warning" data-testid="am-round-left-out">
          {t("left.round", { n: String(round.notIncluded) })}
        </p>
      ) : null}
      {latest && !started ? (
        <div className="flex flex-col items-center gap-1">
          <Button type="button" size="sm" onClick={start} loading={busy} data-testid="am-round-start">
            {busy ? null : <RotateCw width={13} height={13} aria-hidden />}
            <span>{busy ? t("round.starting") : t("round.start")}</span>
          </Button>
          <p className="text-center text-[11px] text-text-muted">{t("round.carry")}</p>
          {error ? (
            <p role="alert" className="text-center text-xs text-status-danger">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** An older round, folded into its own divider line; opened on demand. */
export function FoldedRound({ round, children }: { round: RoundInfo; children: React.ReactNode }) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2" data-testid="am-round-folded" data-open={open ? "true" : "false"} data-outcome={round.outcome} data-run={round.runId}>
      <div className="flex items-center gap-3">
        <span className="h-px flex-1 bg-border-subtle" aria-hidden />
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs text-text-secondary hover:text-text-primary"
        >
          <span>{roundLabel(t, round)}</span>
          <span className="text-text-muted">{`· ${open ? t("round.hide") : t("round.show")}`}</span>
          <ChevronDown width={13} height={13} aria-hidden className={cls("transition-transform", open ? "rotate-180" : "")} />
        </button>
        <span className="h-px flex-1 bg-border-subtle" aria-hidden />
      </div>
      {!open && round.artifacts.length && round.kind === "run" ? (
        <div className="flex flex-wrap justify-center gap-1.5" aria-label={t("round.files")}>
          {round.artifacts.map((a) => (
            <FileLink key={a.id} a={a} />
          ))}
        </div>
      ) : null}
      {!open && round.notIncluded > 0 && round.kind === "run" ? (
        <p className="text-center text-xs text-status-warning" data-testid="am-round-left-out">
          {t("left.round", { n: String(round.notIncluded) })}
        </p>
      ) : null}
      {open ? <div className="flex flex-col gap-5 border-l-2 border-border-subtle pl-3">{children}</div> : null}
    </div>
  );
}

/** Files or an instruction after a finished round: a question, not a guess. */
export function IntentChip({ item }: { item: ThreadItem }) {
  const t = useAmT();
  const ws = useWorkspace();
  const p = (item.payload ?? {}) as IntentPayload;
  const [busy, setBusy] = useState<"new_round" | "about_last" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const period = p.last_round?.period_label?.trim() ?? "";
  // the sentence for a round that names its period, and the one for a round that does not
  const body = p.files?.length
    ? period
      ? t("intent.bodyFiles", { period })
      : t("intent.bodyFilesNoPeriod")
    : period
      ? t("intent.bodyText", { period })
      : t("intent.bodyTextNoPeriod");
  const open = p.status === "open";
  const choose = (choice: "new_round" | "about_last") => {
    setBusy(choice);
    setError(null);
    amApi.answerIntent(item.id, choice).then(
      () => setBusy(null),
      (err) => {
        setBusy(null);
        setError(`${t("intent.failed")}: ${errorMessage(err)}`);
      },
    );
  };
  if (!open) {
    return (
      <div className="text-xs text-text-muted" data-testid="am-intent" data-status="chosen">
        <p>{p.choice === "new_round" ? t("intent.chosenNew") : t("intent.chosenAbout")}</p>
        {p.note ? <p className="mt-0.5">{p.note}</p> : null}
      </div>
    );
  }
  return (
    <div
      role="group"
      aria-label={t("intent.title")}
      className="rounded-xl border border-border-strong bg-bg-surface px-4 py-3"
      data-testid="am-intent"
      data-status="open"
    >
      <p className="text-sm font-semibold text-text-primary">{t("intent.title")}</p>
      <p className="mt-0.5 text-sm text-text-secondary">{body}</p>
      {p.fresh ? <p className="mt-1.5 text-xs text-text-secondary">{t("intent.fresh")}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={() => choose("new_round")} loading={busy === "new_round"} disabled={!!busy} data-testid="am-intent-new">
          <span>{t("intent.new")}</span>
        </Button>
        <Button type="button" variant="secondary" size="sm" onClick={() => choose("about_last")} loading={busy === "about_last"} disabled={!!busy} data-testid="am-intent-about">
          <span>{t("intent.about")}</span>
        </Button>
      </div>
      <p className="mt-2 text-[11px] text-text-muted">{t("round.carry")}</p>
      {error ? (
        <p role="alert" className="mt-1 text-xs text-status-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
