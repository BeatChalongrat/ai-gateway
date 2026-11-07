"use client";

// AM12 — the comparison a choice card asks about.
//
// One row per candidate, in the order the agent's package gave them (fewest
// points still not known first, then the lowest total it could work out) —
// and the card says that this is an order, not advice: nothing here marks a
// row as the one to take. What is not known is written "ไม่ทราบ", never left
// blank and never a guess. Each row opens to everything else the page said
// (each a quote from the saved page), what is still not known and why, the
// values a reader gave that were not used because the saved page does not
// hold them, and the picture cut from the saved page where the price was
// read. Sources that did not become a candidate are listed with why.
//
// A page's address is shown as text and opens nothing by itself: a link out
// is a deliberate click on an address the person can read first.

import { useEffect, useId, useState } from "react";
import { ChevronDown, ImageOff } from "lucide-react";

import { Spinner } from "@/components/ui/Spinner";

import { amApi } from "../api";
import { formatDateTime, formatMoney2 } from "../format";
import { useAmLocale, useAmT, type AmKey, type AmT } from "../i18n";
import type { Comparison, ComparisonCandidate, PageQuote } from "../types";
import { cls, Pill } from "../ui";

// the table's headings, by their keys (the two that hold amounts are set to the right)
const COLUMNS: readonly { key: AmKey; right?: boolean }[] = [
  { key: "cmp.col.no" },
  { key: "cmp.col.source" },
  { key: "cmp.col.item" },
  { key: "cmp.col.price", right: true },
  { key: "cmp.col.vat" },
  { key: "cmp.col.total", right: true },
  { key: "cmp.col.open" },
];

/** A question's evidence, when it is a comparison. */
export function comparisonOf(evidence: unknown): Comparison | null {
  if (!evidence || typeof evidence !== "object") return null;
  const c = (evidence as { comparison?: unknown }).comparison;
  if (!c || typeof c !== "object" || !Array.isArray((c as Comparison).candidates)) return null;
  return c as Comparison;
}

function money(v: string | null | undefined, currency: string | null | undefined): string | null {
  if (v === null || v === undefined || v === "") return null;
  return `${currency ? `${currency} ` : ""}${formatMoney2(v)}`;
}

export function ComparisonTable({ comparison, runId }: { comparison: Comparison; runId: string }) {
  const t = useAmT();
  const [open, setOpen] = useState<string | null>(null);
  const unknown = t("cmp.unknown");
  const { item, candidates, dropped, round } = comparison;
  const used = round?.used ?? {};
  return (
    <section className="mb-2 flex flex-col gap-2" data-testid="am-comparison" aria-label={t("cmp.title")}>
      <header className="flex flex-col gap-0.5">
        <h3 className="text-sm font-semibold text-text-primary">{t("cmp.title")}</h3>
        <p className="text-xs text-text-secondary">
          {item.quantity
            ? t("cmp.for", { item: item.name, quantity: item.quantity, unit: item.unit ?? "" }).trim()
            : t("cmp.forNoQty", { item: item.name })}
        </p>
        <p className="text-xs text-text-muted" data-testid="am-comparison-order">
          {t("cmp.order")}
        </p>
      </header>
      <div className="scrollbar-thin overflow-x-auto rounded-xl border border-border-subtle">
        <table className="w-full min-w-[640px] border-collapse text-left text-xs">
          <thead className="bg-bg-elevated text-text-secondary">
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} scope="col" className={cls("px-2.5 py-2 font-medium", c.right && "text-right")}>
                  {t(c.key)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => (
              <CandidateRows key={c.key} c={c} runId={runId} unknown={unknown} open={open === c.key} onToggle={() => setOpen(open === c.key ? null : c.key)} />
            ))}
          </tbody>
        </table>
      </div>
      {round?.lines?.length ? (
        <ul className="flex flex-col gap-0.5 text-xs text-status-warning" data-testid="am-comparison-limits">
          {round.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
      {typeof used.pages === "number" ? (
        <p className="text-xs text-text-muted">{t("cmp.used", { searches: String(used.searches ?? 0), pages: String(used.pages) })}</p>
      ) : null}
      {dropped.length ? (
        <details className="rounded-xl border border-border-subtle px-3 py-2 text-xs" data-testid="am-comparison-dropped">
          <summary className="cursor-pointer select-none text-text-secondary">{t("cmp.dropped", { n: String(dropped.length) })}</summary>
          <ul className="mt-2 flex flex-col gap-1.5">
            {dropped.map((d, i) => (
              <li key={`${d.url}:${i}`} className="flex flex-col">
                <span className="break-all font-medium text-text-primary">{d.host || d.url}</span>
                {/* why, in the package's words; never the reason's code */}
                {d.words ? <span className="text-text-secondary">{d.words}</span> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function CandidateRows({
  c,
  runId,
  unknown,
  open,
  onToggle,
}: {
  c: ComparisonCandidate;
  runId: string;
  unknown: string;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useAmT();
  const panel = useId();
  const total = money(c.total, c.price?.currency);
  const points = c.words?.open?.length ?? c.open.length;
  return (
    <>
      <tr className="border-t border-border-subtle align-top" data-testid="am-candidate" data-candidate={c.key}>
        <td className="px-2.5 py-2 tabular-nums text-text-secondary">{c.no}</td>
        <td className="px-2.5 py-2">
          <span className="break-all text-text-primary">{c.host}</span>
          {c.seller?.quote ? <span className="block text-text-muted">{c.seller.quote}</span> : null}
        </td>
        <td className="max-w-[16rem] px-2.5 py-2 text-text-primary">
          {c.name}
          {c.model?.quote ? <span className="block text-text-muted">{c.model.quote}</span> : null}
        </td>
        <td className="whitespace-nowrap px-2.5 py-2 text-right tabular-nums">
          {c.price?.display ? <span className="text-text-primary">{c.price.display}</span> : <Unknown text={unknown} />}
        </td>
        <td className="px-2.5 py-2">{c.vat ? <span className="text-text-primary">{c.words?.vat ?? c.vat.says}</span> : <Unknown text={c.words?.vat ?? unknown} />}</td>
        <td className="whitespace-nowrap px-2.5 py-2 text-right tabular-nums">
          {total ? <span className="font-medium text-text-primary">{total}</span> : <Unknown text={unknown} />}
        </td>
        <td className="px-2.5 py-2">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            aria-controls={panel}
            aria-label={t("cmp.details", { n: String(c.no) })}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-text-secondary hover:bg-bg-elevated"
            data-testid="am-candidate-toggle"
          >
            {points > 0 ? <Pill tone="warning">{t("cmp.openCount", { n: String(points) })}</Pill> : <Pill tone="success">{t("cmp.openNone")}</Pill>}
            <ChevronDown width={14} height={14} className={cls("transition-transform", open && "rotate-180")} aria-hidden />
          </button>
        </td>
      </tr>
      {open ? (
        <tr className="border-t border-border-subtle bg-bg-surface" data-testid="am-candidate-details">
          <td colSpan={COLUMNS.length} id={panel} className="px-3 py-3">
            <CandidateDetails c={c} runId={runId} unknown={unknown} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function Unknown({ text }: { text: string }) {
  return (
    <span className="text-text-muted" data-testid="am-unknown">
      {text}
    </span>
  );
}

function said(q: PageQuote | null | undefined): string | null {
  return q?.quote ? q.quote : null;
}

function CandidateDetails({ c, runId, unknown }: { c: ComparisonCandidate; runId: string; unknown: string }) {
  const t = useAmT();
  const locale = useAmLocale();
  const currency = c.price?.currency;
  const facts: { key: AmKey; value: string | null }[] = [
    { key: "cmp.field.page", value: c.url },
    { key: "cmp.field.observed", value: c.observed_at ? formatDateTime(c.observed_at, locale) : null },
    { key: "cmp.field.kind", value: c.words?.kind ?? null },
    { key: "cmp.field.seller", value: said(c.seller) },
    { key: "cmp.field.legalName", value: said(c.legal_name) },
    { key: "cmp.field.taxId", value: said(c.tax_id) },
    { key: "cmp.field.onList", value: c.words?.on_list ?? null },
    { key: "cmp.field.model", value: said(c.model) },
    { key: "cmp.field.variant", value: said(c.variant) },
    { key: "cmp.field.basis", value: said(c.price_basis) },
    { key: "cmp.field.subtotal", value: money(c.subtotal, currency) },
    { key: "cmp.field.net", value: money(c.net, currency) },
    { key: "cmp.field.vatPart", value: money(c.vat_part, currency) },
    { key: "cmp.field.total", value: money(c.total, currency) },
    { key: "cmp.field.shipping", value: said(c.shipping) },
    { key: "cmp.field.leadTime", value: said(c.lead_time) },
    { key: "cmp.field.minimum", value: said(c.minimum_order) },
    { key: "cmp.field.stock", value: said(c.stock) },
    { key: "cmp.field.warranty", value: said(c.warranty) },
    { key: "cmp.field.requirements", value: c.words?.requirements ?? null },
  ];
  return (
    <div className="flex flex-col gap-3 text-xs">
      {c.picture?.name ? <EvidencePicture runId={runId} name={c.picture.name} label={t("cmp.picture")} t={t} /> : null}
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-[minmax(9rem,auto)_1fr]">
        {facts.map((f) => (
          <div key={f.key} className="contents">
            <dt className="text-text-muted">{t(f.key)}</dt>
            <dd className={cls("break-words", f.value ? "text-text-primary" : "text-text-muted")}>{f.value ?? unknown}</dd>
          </div>
        ))}
      </dl>
      {c.words?.open?.length ? (
        <div data-testid="am-candidate-open">
          <p className="font-medium text-text-primary">{t("cmp.openHeading")}</p>
          <ul className="mt-1 flex flex-col gap-1">
            {c.words.open.map((o) => (
              <li key={o.code}>
                <span className="font-medium text-text-secondary">{o.what}</span>
                <span className="text-text-secondary"> — {o.why}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {c.words?.not_found?.length ? (
        <div data-testid="am-candidate-not-found">
          <p className="font-medium text-text-primary">{t("cmp.notFoundHeading")}</p>
          <ul className="mt-1 flex flex-col gap-1">
            {c.words.not_found.map((n, i) => (
              <li key={`${n.field}:${i}`}>
                <span className="font-medium text-text-secondary">{n.field}</span>
                <span className="text-text-secondary"> — {n.why}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** A picture that belongs to the run's owner: fetched with the session, shown from memory. */
export function EvidencePicture({ runId, name, label, t, load }: { runId: string; name: string; label: string; t: AmT; load?: () => Promise<string> }) {
  const [state, setState] = useState<{ url: string | null; failed: boolean }>({ url: null, failed: false });
  useEffect(() => {
    let live = true;
    let made: string | null = null;
    (load ? load() : amApi.evidencePicture(runId, name)).then(
      (url) => {
        made = url;
        if (live) setState({ url, failed: false });
        else URL.revokeObjectURL(url);
      },
      () => {
        if (live) setState({ url: null, failed: true });
      },
    );
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
    // `load` is a new function each render; the picture is named by run and name
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, name]);
  if (state.failed) {
    return (
      <p className="flex items-center gap-1.5 text-text-muted" data-testid="am-evidence-picture-failed">
        <ImageOff width={14} height={14} aria-hidden />
        {t("cmp.pictureFailed")}
      </p>
    );
  }
  if (!state.url) {
    return (
      <p role="status" className="flex items-center gap-1.5 text-text-muted">
        <Spinner size={13} />
        {t("cmp.pictureLoading")}
      </p>
    );
  }
  return (
    <figure className="flex flex-col gap-1" data-testid="am-evidence-picture">
      {/* a picture of evidence: its own size, never stretched; a blob URL is not something next/image can take */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={state.url} alt={label} className="max-h-72 w-auto max-w-full rounded-lg border border-border-subtle object-contain" />
      <figcaption className="text-text-muted">{label}</figcaption>
    </figure>
  );
}
