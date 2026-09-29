"use client";

// The delivered-files card: the period, the quality gate, the three key
// numbers, each file with its version and a download, and "more files"
// (a re-render from the SAME computed numbers — nothing is recomputed).
//
// AM6 — a round that finished open is a draft that is done, not an alert;
// the invoice ↔ PO agent says how deep its lines were tested (./PoMatch);
// a finished round's card offers "ร่างอีเมล" (./EmailDraftCard).

import { useEffect, useId, useRef, useState } from "react";
import { BadgeCheck, ChevronDown, CircleCheck, Download, Plus, TriangleAlert } from "lucide-react";

import { Button, ButtonLink } from "@/components/ui/Button";
import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { Spinner } from "@/components/ui/Spinner";
import { formatBytes } from "@/lib/fileUtil";

import { amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import { extensionOf, formatCredits, formatMoney, formatMoney2 } from "../format";
import { COMPLETED_OPEN, delivered } from "../outcome";
import type { NotIncluded, OutputArtifact, OutputsPayload, QualityCheck } from "../types";
import { cls, Pill } from "../ui";
import { SignOffButton } from "../review/SignOff";
import { provenanceHref, useWorkspace } from "./context";
import { DraftEmailButton } from "./EmailDraftCard";
import { personText } from "./personText";
import { PO_KIND, PoSummary, ReceiptOffers } from "./PoMatch";

export const FORMAT_KEYS: Record<string, AmKey> = {
  xlsx: "outputs.fmt.xlsx",
  docx: "outputs.fmt.docx",
  pptx: "outputs.fmt.pptx",
  pdf: "outputs.fmt.pdf",
  template: "outputs.fmt.template",
};
// The menu still offers no PDF: the server makes one only while a runner has
// the doc-converter (AM2), which this card cannot know; a delivered PDF is labelled.
export const MORE_FORMATS = ["xlsx", "docx", "pptx"];

// Formats a file name does not explain: a filled template (its name ends in
// the template's own extension) and the PDF (the report, converted). Their
// rows say what they are; the office files' names already do.
const ROW_LABELLED = new Set(["template", "pdf"]);

export function artifactLabelKey(format: string): AmKey | null {
  return ROW_LABELLED.has(format) ? FORMAT_KEYS[format] ?? null : null;
}

/** The extension a row's icon shows: a filled template's own (…_template_v1.xlsx), else the format's. */
export function artifactExtension(a: { format: string; name: string }): string {
  if (a.format === "template") {
    const dot = a.name.lastIndexOf(".");
    if (dot >= 0 && dot < a.name.length - 1) return a.name.slice(dot).toLowerCase();
  }
  return extensionOf(a.format);
}

export function qualityOf(artifacts: { quality?: { checks?: QualityCheck[] } | null }[]): { pass: number; total: number; checks: QualityCheck[] } {
  const checks = artifacts.flatMap((a) => a.quality?.checks ?? []);
  return { pass: checks.filter((c) => c.ok).length, total: checks.length, checks };
}

export function OutputsCard({
  outputs,
  superseded = false,
  outcome,
}: {
  outputs: OutputsPayload;
  /** AM5: a newer files card of the same round exists below (this one's list is history). */
  superseded?: boolean;
  /** AM6: what the card's round came to (absent: the card stands outside a thread's rounds). */
  outcome?: string;
}) {
  const t = useAmT();
  const ws = useWorkspace();
  const canSignOff = !!ws && !ws.selfTestRuns?.has(outputs.run_id);
  // AM6: the round finished and delivered this draft — said as a state, not raised as an alert
  const finishedOpen = outcome === COMPLETED_OPEN;
  // AM6: a drafted e-mail about the round's own files card (not a re-render's, not a replaced one)
  const canDraftEmail = canSignOff && !!outcome && delivered(outcome) && !outputs.rerender && !superseded;
  const artifacts = outputs.artifacts ?? [];
  const summary = outputs.summary ?? {};
  const period = outputs.period_label || summary.period_label || "";
  const q = qualityOf(artifacts);
  // AM4: checks, credits and model names are trust signals, not daily reading —
  // folded for members, open for admins. Which model saw the documents is not
  // folded: that line always shows.
  const [detailsOpen, setDetailsOpen] = useState(!!ws?.admin);
  const detailsId = useId();

  // AM5: a reconciliation's three numbers are its two adjusted balances and what is between them
  const reconciliation = summary.kind === "bank_reconciliation";
  // AM6: the invoice ↔ PO agent's counts and how deep its lines were tested
  const po = summary.kind === PO_KIND;
  const allKpis: { key: AmKey; value: unknown }[] = reconciliation
    ? [
        { key: "outputs.adjustedBank", value: summary.adjusted_bank },
        { key: "outputs.adjustedBook", value: summary.adjusted_book },
        { key: "outputs.difference", value: summary.difference },
      ]
    : [
        { key: "outputs.revenue", value: summary.revenue },
        { key: "outputs.expenses", value: summary.total_expenses },
        { key: "outputs.netProfit", value: summary.net_profit },
      ];
  const kpis = po ? [] : allKpis.filter((k) => k.value !== undefined && k.value !== null);
  const gate = outputs.gate ?? null;
  const isDraft = !!outputs.draft;
  const count = (v: unknown) => (typeof v === "number" ? String(v) : "0");
  const modelLine = outputs.model?.model ? `${outputs.model.model} · ${outputs.model.self_hosted ? t("ws.modelInOrg") : t("ws.modelExternal")}` : null;
  const creditLine = outputs.credits ? t("outputs.credits", { n: formatCredits(outputs.credits) }) : null;
  const hasDetails = q.total > 0 || !!creditLine || !!modelLine;

  return (
    <div className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-outputs">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1 text-sm font-semibold text-text-primary">
          {period ? t("outputs.title", { period }) : t("outputs.titleNoPeriod")}
        </div>
      </div>
      {outputs.review ? (
        <div className="mt-2 rounded-lg border border-status-success/40 bg-status-success/10 px-3 py-2 text-xs text-text-primary" data-testid="am-outputs-review">
          <p className="flex items-center gap-1.5 font-medium">
            <BadgeCheck width={14} height={14} className="text-status-success" aria-hidden />
            <span>{t("review.outputsBadge", { name: outputs.review.reviewer, when: outputs.review.at_label })}</span>
          </p>
          {outputs.review.note ? <p className="mt-0.5 text-text-secondary">{t("review.noteLine", { note: outputs.review.note })}</p> : null}
          {outputs.review.skipped?.length ? (
            <p className="mt-0.5 text-text-secondary">{t("review.skipped", { files: outputs.review.skipped.join(", ") })}</p>
          ) : null}
        </div>
      ) : outputs.rerender ? (
        <p className="mt-1 text-xs text-text-secondary">{t("outputs.rerender")}</p>
      ) : null}

      {isDraft ? (
        <div role={finishedOpen ? "status" : "alert"} className="mt-2 rounded-lg border border-status-warning/40 bg-status-warning/10 px-3 py-2.5" data-testid="am-outputs-draft">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
            <TriangleAlert width={15} height={15} className="shrink-0 text-status-warning" aria-hidden />
            <span>{finishedOpen ? t("outputs.draftDone") : t("outputs.draft")}</span>
          </p>
          {personText(gate?.what ?? "") ? <p className="mt-1 text-sm text-text-primary">{personText(gate?.what ?? "")}</p> : null}
          {personText(gate?.next ?? "") ? <p className="mt-0.5 text-xs text-text-secondary">{t("outputs.draftNext", { next: personText(gate?.next ?? "") })}</p> : null}
        </div>
      ) : gate?.passed && personText(gate.what) ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-text-primary" data-testid="am-outputs-gate">
          <CircleCheck width={14} height={14} className="shrink-0 text-status-success" aria-hidden />
          <span>{personText(gate.what)}</span>
        </p>
      ) : null}

      {kpis.length ? (
        <dl className="mt-3 grid grid-cols-3 gap-2" data-testid="am-outputs-kpis">
          {kpis.map((k) => (
            <div key={String(k.key)} className="min-w-0 rounded-lg bg-bg-base px-2.5 py-2">
              <dt className="truncate text-[11px] text-text-muted">{t(k.key)}</dt>
              <dd className="truncate text-sm font-semibold tabular-nums text-text-primary sm:text-base">
                {/* a reconciliation is read to the satang */}
                {reconciliation ? formatMoney2(k.value) : formatMoney(k.value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {reconciliation ? (
        <p className="mt-2 text-xs text-text-secondary" data-testid="am-outputs-match">
          {t("outputs.matchLine", {
            matched: count(summary.matched),
            rule: count(summary.by_rule),
            person: count(summary.by_person),
            items: count(summary.items),
          })}
          {Number(summary.suspect) > 0 ? ` · ${t("outputs.suspect", { n: count(summary.suspect) })}` : ""}
        </p>
      ) : null}

      {po ? <PoSummary summary={summary} /> : null}

      {artifacts.length ? (
        <ul className="mt-3 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle">
          {artifacts.map((a) => (
            <ArtifactRow key={a.id} artifact={a} />
          ))}
        </ul>
      ) : null}

      <LeftOut runId={outputs.run_id} docs={outputs.summary?.not_included ?? []} superseded={superseded} />

      {po && Number(summary.receipt_offers) > 0 && !superseded && !outputs.rerender ? <ReceiptOffers runId={outputs.run_id} /> : null}

      <SeenBy outputs={outputs} />

      {hasDetails ? (
        <div className="mt-2">
          <button
            type="button"
            aria-expanded={detailsOpen}
            aria-controls={detailsId}
            onClick={() => setDetailsOpen((v) => !v)}
            className="focus-ring inline-flex items-center gap-1 rounded-md py-0.5 text-xs text-text-secondary hover:text-text-primary"
            data-testid="am-outputs-details"
          >
            <ChevronDown width={14} height={14} aria-hidden className={cls("transition-transform", detailsOpen ? "rotate-180" : "")} />
            <span>{t("outputs.details")}</span>
          </button>
          {detailsOpen ? (
            <div id={detailsId} className="mt-1.5 flex flex-col gap-1.5" data-testid="am-outputs-details-body">
              {q.total > 0 ? (
                <div>
                  <Pill tone={q.pass === q.total ? "success" : "warning"}>
                    {q.pass === q.total ? <CircleCheck width={12} height={12} aria-hidden /> : <TriangleAlert width={12} height={12} aria-hidden />}
                    <span>{t("outputs.quality", { pass: String(q.pass), total: String(q.total) })}</span>
                  </Pill>
                  <ul className="mt-1.5 flex flex-col gap-1">
                    {q.checks.map((c, i) => (
                      <li key={`${c.name ?? c.label}-${i}`} className="flex items-start gap-2 text-xs">
                        {c.ok ? (
                          <CircleCheck width={14} height={14} className="mt-px shrink-0 text-status-success" aria-hidden />
                        ) : (
                          <TriangleAlert width={14} height={14} className="mt-px shrink-0 text-status-warning" aria-hidden />
                        )}
                        <span className="min-w-0 text-text-secondary">
                          <span className="text-text-primary">{personText(c.label ?? "") || t("outputs.check")}</span>
                          {personText(c.detail ?? "") ? <span>{` · ${personText(c.detail ?? "")}`}</span> : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {modelLine || creditLine ? (
                <p className="text-[11px] text-text-muted">{[modelLine, creditLine].filter(Boolean).join(" · ")}</p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
        {/* a draft is neither signed nor made into other files: the agent's gate did not pass */}
        {!isDraft ? <MoreFilesMenu runId={outputs.run_id} /> : null}
        {canSignOff && !outputs.review && !isDraft ? <SignOffButton runId={outputs.run_id} /> : null}
        <ButtonLink href={provenanceHref(outputs.run_id)} variant="secondary" size="sm">
          {t("ws.provenance")}
        </ButtonLink>
        {canDraftEmail ? <DraftEmailButton runId={outputs.run_id} /> : null}
        {canSignOff && !outputs.review && isDraft ? <SignOffUnavailable why={gate?.what} /> : null}
      </div>
    </div>
  );
}

/** AM6 — a round whose files are a draft is not signed: said where the
 *  sign-off would be, with the agent's own sentence as the reason. */
export function SignOffUnavailable({ why }: { why?: string | null }) {
  const t = useAmT();
  const reason = personText(why ?? "");
  return (
    // order-last + w-full: its own line under the row's buttons.
    <p className="order-last w-full text-[11px] text-text-muted" data-testid="am-signoff-unavailable">
      {reason ? t("review.unavailableWhy", { why: reason }) : t("review.unavailable")}
    </p>
  );
}

/** AM5 — "เอกสารที่ยังไม่ได้รวม": every document the round left out, why, and
 *  one click to bring it back. The list is the round's own (its computed
 *  table), so it is there after a reload, on the other client, next month. */
export function LeftOut({ runId, docs, superseded }: { runId: string; docs: NotIncluded[]; superseded: boolean }) {
  const t = useAmT();
  if (!docs.length) return null;
  return (
    <section
      className="mt-3 rounded-lg border border-status-warning/40 bg-status-warning/10 px-3 py-2.5"
      aria-label={t("left.title", { n: String(docs.length) })}
      data-testid="am-not-included"
    >
      <h3 className="text-sm font-semibold text-text-primary" data-testid="am-not-included-title">
        {t("left.title", { n: String(docs.length) })}
      </h3>
      <p className="mt-0.5 text-xs text-text-secondary">{superseded ? t("left.superseded") : t("left.body")}</p>
      <ul className="mt-2 flex flex-col divide-y divide-border-subtle">
        {docs.map((d) => (
          <LeftOutRow key={d.doc_key} runId={runId} doc={d} superseded={superseded} />
        ))}
      </ul>
    </section>
  );
}

function LeftOutRow({ runId, doc, superseded }: { runId: string; doc: NotIncluded; superseded: boolean }) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const restore = () => {
    setBusy(true);
    setNote(null);
    amApi.restore(runId, doc.doc_key).then(
      (r) => {
        setBusy(false);
        setNote({ text: r?.asks ? t("left.restoredAsk") : t("left.restored"), error: false });
      },
      (err) => {
        setBusy(false);
        setNote({ text: errorMessage(err), error: true });
      },
    );
  };
  const done = !!note && !note.error;
  return (
    <li className="flex flex-wrap items-start gap-x-3 gap-y-1.5 py-2" data-testid="am-not-included-row" data-doc={doc.doc_key}>
      <FileTypeBadge filename={doc.name} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="break-words text-sm text-text-primary">{doc.name}</div>
        <div className="text-xs text-text-secondary">{doc.detail ? `${doc.reason} · ${doc.detail}` : doc.reason}</div>
        <div className="text-[11px] text-text-muted">{doc.how}</div>
        {note ? (
          <div role={note.error ? "alert" : "status"} className={cls("mt-1 text-xs", note.error ? "text-status-danger" : "text-status-success")} data-testid="am-restore-note">
            {note.text}
          </div>
        ) : null}
      </div>
      {doc.can_restore && !superseded && !done ? (
        <Button type="button" variant="secondary" size="sm" onClick={restore} loading={busy} data-testid="am-restore">
          <span>{busy ? t("left.restoring") : t("left.restore")}</span>
        </Button>
      ) : null}
    </li>
  );
}

/** AM4: the one line that always shows — which model saw the documents (or that none did). */
export function SeenBy({ outputs }: { outputs: OutputsPayload }) {
  const t = useAmT();
  const m = outputs.model ?? {};
  const where = (self?: boolean) => (self ? t("where.inOrg") : t("where.outside"));
  const summaryModels = (outputs.summary?.models as { model?: string; self_hosted?: boolean }[] | undefined) ?? [];
  const models = summaryModels.filter((x) => !!x.model);
  const lines: string[] = [];
  if (m.uses === "text") {
    lines.push(m.model ? t("outputs.writtenBy", { model: m.model, where: where(m.self_hosted) }) : t("outputs.writtenByRules"));
  } else if (models.length) {
    for (const x of models) lines.push(t("outputs.seenBy", { model: x.model ?? "", where: where(x.self_hosted) }));
  } else {
    lines.push(t("outputs.seenNone"));
  }
  if (m.not_chosen && m.chosen_model) {
    const used = m.uses === "text" ? m.model : m.vision_model;
    lines.push(
      used
        ? t("outputs.notChosen", { chosen: m.chosen_model, used, why: t("outputs.notChosenWhy") })
        : t("outputs.noneInstead", { chosen: m.chosen_model }),
    );
  }
  return (
    <div className="mt-2 flex flex-col gap-0.5" data-testid="am-outputs-seen-by">
      {lines.map((l) => (
        <p key={l} className="text-xs text-text-secondary">
          {l}
        </p>
      ))}
    </div>
  );
}

export function ArtifactRow({
  artifact,
  download: downloadWith,
}: {
  artifact: Pick<OutputArtifact, "id" | "name" | "format" | "size" | "version">;
  /** AM4: an admin's reason-gated download instead of the owner's. */
  download?: () => Promise<void>;
}) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const download = () => {
    setBusy(true);
    setError(null);
    (downloadWith ? downloadWith() : amApi.downloadArtifact(artifact.id, artifact.name)).then(
      () => setBusy(false),
      (err) => {
        setBusy(false);
        setError(errorMessage(err));
      },
    );
  };
  const label = artifactLabelKey(artifact.format);
  return (
    <li className="flex items-center gap-3 px-3 py-2" data-testid="am-artifact" data-format={artifact.format}>
      <FileTypeBadge extension={artifactExtension(artifact)} filename={artifact.name} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-text-primary" title={artifact.name}>
          {artifact.name}
        </div>
        <div className="text-[11px] text-text-muted">
          {label ? `${t(label)} · ` : null}
          {formatBytes(artifact.size)}
          {` · `}
          {t("outputs.version", { n: String(artifact.version) })}
        </div>
        {error ? (
          <div role="alert" className="text-[11px] text-status-danger">
            {error}
          </div>
        ) : null}
      </div>
      <button
        type="button"
        onClick={download}
        disabled={busy}
        aria-label={t("common.downloadNamed", { name: artifact.name })}
        className="focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-text-secondary hover:bg-bg-elevated hover:text-text-primary disabled:opacity-50"
      >
        {busy ? <Spinner size={15} /> : <Download width={16} height={16} aria-hidden />}
      </button>
    </li>
  );
}

function MoreFilesMenu({ runId }: { runId: string }) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const request = (fmt: string) => {
    setOpen(false);
    setBusy(true);
    setNote(null);
    amApi.formats(runId, [fmt]).then(
      () => {
        setBusy(false);
        setNote({ text: t("outputs.requested", { fmt: t(FORMAT_KEYS[fmt] ?? "outputs.fmt.xlsx") }), error: false });
      },
      (err) => {
        setBusy(false);
        setNote({ text: errorMessage(err), error: true });
      },
    );
  };

  return (
    <>
      <div ref={ref} className="relative">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          loading={busy}
          onClick={() => setOpen((v) => !v)}
          data-testid="am-more-files"
        >
          {busy ? null : <Plus width={13} height={13} aria-hidden />}
          <span>{t("outputs.moreFiles")}</span>
        </Button>
        {open ? (
          <div id={menuId} role="menu" className="absolute bottom-full left-0 z-20 mb-1.5 w-52 overflow-hidden rounded-xl border border-border bg-bg-surface py-1 shadow-lg">
            {MORE_FORMATS.map((fmt) => (
              <button
                key={fmt}
                type="button"
                role="menuitem"
                onClick={() => request(fmt)}
                className="focus-ring flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-elevated"
              >
                <FileTypeBadge extension={extensionOf(fmt)} size="sm" />
                <span>{t(FORMAT_KEYS[fmt] ?? "outputs.fmt.xlsx")}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {note ? (
        // order-last + w-full: its own line under the card's button row.
        <p role="status" className={cls("order-last w-full text-[11px]", note.error ? "text-status-danger" : "text-text-secondary")}>
          {note.text}
        </p>
      ) : null}
    </>
  );
}
