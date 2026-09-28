"use client";

// AM4 — the pieces of a round boundary: the divider that closes a finished
// round (its files, and "เริ่มรอบใหม่" one click away), an older round folded
// into that same line, and the chip that asks — never guesses — whether what
// arrived after a finished round starts a new one.

import { useState } from "react";
import { ChevronDown, Download, RotateCcw } from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";

import { amApi, errorMessage } from "../api";
import { extensionOf } from "../format";
import { useAmT } from "../i18n";
import type { IntentPayload, OutputArtifact, ThreadItem } from "../types";
import { BTN_PRIMARY_SMALL, BTN_SMALL, cls, Spin } from "../ui";
import { useWorkspace } from "./context";
import type { RoundInfo } from "./rounds";

function roundLabel(t: ReturnType<typeof useAmT>, round: RoundInfo): string {
  if (round.kind === "self_test") return t("round.selfTest");
  if (round.status === "succeeded") return round.period ? t("round.done", { period: round.period }) : t("round.doneNoPeriod");
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
      {busy ? <Spin size={11} /> : <Download width={12} height={12} aria-hidden className="shrink-0" />}
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
    <div className="flex flex-col gap-2 py-1" data-testid="am-round-divider" data-status={round.status}>
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
          <button type="button" onClick={start} disabled={busy} className={BTN_PRIMARY_SMALL} data-testid="am-round-start">
            {busy ? <Spin size={12} /> : <RotateCcw width={13} height={13} aria-hidden />}
            <span>{busy ? t("round.starting") : t("round.start")}</span>
          </button>
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
    <div className="flex flex-col gap-2" data-testid="am-round-folded" data-open={open ? "true" : "false"}>
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
  const period = p.last_round?.period_label ? ` ${p.last_round.period_label} ` : "นี้";
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
      <p className="mt-0.5 text-sm text-text-secondary">
        {p.files?.length ? t("intent.bodyFiles", { period }) : t("intent.bodyText", { period })}
      </p>
      {p.fresh ? <p className="mt-1.5 text-xs text-text-secondary">{t("intent.fresh")}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={() => choose("new_round")} disabled={!!busy} className={BTN_PRIMARY_SMALL} data-testid="am-intent-new">
          {busy === "new_round" ? <Spin size={12} /> : null}
          <span>{t("intent.new")}</span>
        </button>
        <button type="button" onClick={() => choose("about_last")} disabled={!!busy} className={BTN_SMALL} data-testid="am-intent-about">
          {busy === "about_last" ? <Spin size={12} /> : null}
          <span>{t("intent.about")}</span>
        </button>
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
