"use client";

// The delivered-files card: the period, the quality gate, the three key
// numbers, each file with its version and a download, and "more files"
// (a re-render from the SAME computed numbers — nothing is recomputed).

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, CircleCheck, Download, Plus, TriangleAlert } from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { formatBytes } from "@/lib/fileUtil";

import { amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import { extensionOf, formatCredits, formatMoney } from "../format";
import type { OutputArtifact, OutputsPayload, QualityCheck } from "../types";
import { BTN_SMALL, cls, Pill, Spin } from "../ui";
import { runHref } from "./context";

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

export function OutputsCard({ outputs }: { outputs: OutputsPayload }) {
  const t = useAmT();
  const artifacts = outputs.artifacts ?? [];
  const summary = outputs.summary ?? {};
  const period = outputs.period_label || summary.period_label || "";
  const q = qualityOf(artifacts);
  const [checksOpen, setChecksOpen] = useState(false);
  const checksId = useId();

  const allKpis: { key: AmKey; value: unknown }[] = [
    { key: "outputs.revenue", value: summary.revenue },
    { key: "outputs.expenses", value: summary.total_expenses },
    { key: "outputs.netProfit", value: summary.net_profit },
  ];
  const kpis = allKpis.filter((k) => k.value !== undefined && k.value !== null);

  return (
    <div className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-outputs">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1 text-sm font-semibold text-text-primary">
          {period ? t("outputs.title", { period }) : t("outputs.titleNoPeriod")}
        </div>
        {q.total > 0 ? (
          <Pill tone={q.pass === q.total ? "success" : "warning"}>
            {q.pass === q.total ? <CircleCheck width={12} height={12} aria-hidden /> : <TriangleAlert width={12} height={12} aria-hidden />}
            <span>{t("outputs.quality", { pass: String(q.pass), total: String(q.total) })}</span>
          </Pill>
        ) : null}
      </div>
      {outputs.rerender ? <p className="mt-1 text-xs text-text-secondary">{t("outputs.rerender")}</p> : null}

      {kpis.length ? (
        <dl className="mt-3 grid grid-cols-3 gap-2">
          {kpis.map((k) => (
            <div key={k.key} className="min-w-0 rounded-lg bg-bg-base px-2.5 py-2">
              <dt className="truncate text-[11px] text-text-muted">{t(k.key)}</dt>
              <dd className="truncate text-sm font-semibold tabular-nums text-text-primary sm:text-base">{formatMoney(k.value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {artifacts.length ? (
        <ul className="mt-3 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle">
          {artifacts.map((a) => (
            <ArtifactRow key={a.id} artifact={a} />
          ))}
        </ul>
      ) : null}

      {q.total > 0 ? (
        <div className="mt-2">
          <button
            type="button"
            aria-expanded={checksOpen}
            aria-controls={checksId}
            onClick={() => setChecksOpen((v) => !v)}
            className="focus-ring inline-flex items-center gap-1 rounded-md py-0.5 text-xs text-text-secondary hover:text-text-primary"
          >
            <ChevronDown width={14} height={14} aria-hidden className={cls("transition-transform", checksOpen ? "rotate-180" : "")} />
            <span>{t("outputs.checks")}</span>
          </button>
          {checksOpen ? (
            <ul id={checksId} className="mt-1.5 flex flex-col gap-1">
              {q.checks.map((c, i) => (
                <li key={`${c.name ?? c.label}-${i}`} className="flex items-start gap-2 text-xs">
                  {c.ok ? (
                    <CircleCheck width={14} height={14} className="mt-px shrink-0 text-status-success" aria-hidden />
                  ) : (
                    <TriangleAlert width={14} height={14} className="mt-px shrink-0 text-status-warning" aria-hidden />
                  )}
                  <span className="min-w-0 text-text-secondary">
                    <span className="text-text-primary">{c.label || c.name}</span>
                    {c.detail ? <span>{` · ${c.detail}`}</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
        <MoreFilesMenu runId={outputs.run_id} />
        <Link href={runHref(outputs.run_id)} className={BTN_SMALL}>
          {t("outputs.viewRun")}
        </Link>
        <div className="flex-1" />
        {outputs.model?.model ? (
          <span className="text-[11px] text-text-muted">
            {outputs.model.model}
            {` · `}
            {outputs.model.self_hosted ? t("ws.modelInOrg") : t("ws.modelExternal")}
          </span>
        ) : null}
        {outputs.credits ? <span className="text-[11px] text-text-muted">{t("outputs.credits", { n: formatCredits(outputs.credits) })}</span> : null}
      </div>
    </div>
  );
}

export function ArtifactRow({ artifact }: { artifact: Pick<OutputArtifact, "id" | "name" | "format" | "size" | "version"> }) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const download = () => {
    setBusy(true);
    setError(null);
    amApi.downloadArtifact(artifact.id, artifact.name).then(
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
        {busy ? <Spin size={15} /> : <Download width={16} height={16} aria-hidden />}
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
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          disabled={busy}
          onClick={() => setOpen((v) => !v)}
          className={BTN_SMALL}
          data-testid="am-more-files"
        >
          {busy ? <Spin size={12} /> : <Plus width={13} height={13} aria-hidden />}
          <span>{t("outputs.moreFiles")}</span>
        </button>
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
