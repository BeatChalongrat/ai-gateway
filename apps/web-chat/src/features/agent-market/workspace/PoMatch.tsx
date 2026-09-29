"use client";

// AM6 — what the invoice ↔ PO agent's files card says above its files.
//
// How deep every line was tested is never hidden: a line checked against a
// receipt (three ways) and a line checked against the order only (two ways)
// are counted apart, and when any line was tested two ways the card says,
// in the warning colour, that those lines were not checked against a
// receipt. Then the three counts with their value, why the others did not
// pass, and that approving and paying are a person's.
//
// A receipt confirmed in this system is offered, never required: a folded
// section lists the order lines billed without one. It is not a question —
// nothing waits for it, and the round's result stands without it.

import { useEffect, useId, useState } from "react";
import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

import { amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import { formatInt, formatMoney2 } from "../format";
import type { ReceiptOffer } from "../types";
import { cls } from "../ui";
import { personText } from "./personText";

export const PO_KIND = "po_matching";

interface PoReason {
  code?: string;
  label?: string;
  invoices?: number;
  value?: string | null;
}

const COUNTS: { status: string; key: AmKey; n: string; value: string }[] = [
  { status: "passed", key: "po.passed", n: "passed", value: "value_passed" },
  { status: "blocked", key: "po.blocked", n: "blocked", value: "value_blocked" },
  { status: "person", key: "po.person", n: "person", value: "value_person" },
];

const num = (v: unknown): number => (typeof v === "number" ? v : Number(v) || 0);
const hasValue = (v: unknown) => v !== null && v !== undefined && v !== "";

export function PoSummary({ summary }: { summary: Record<string, unknown> }) {
  const t = useAmT();
  const twoWay = num(summary.two_way);
  const unlinked = num(summary.unlinked);
  const reasons = (Array.isArray(summary.by_reason) ? (summary.by_reason as PoReason[]) : []).filter((r) => !!personText(r.label ?? ""));
  const depth =
    t("po.depth", { three: formatInt(num(summary.three_way)), two: formatInt(twoWay) }) +
    (unlinked > 0 ? t("po.depthUnlinked", { n: formatInt(unlinked) }) : "");
  return (
    <div className="mt-3 flex flex-col gap-2.5" data-testid="am-po">
      <dl className="grid grid-cols-3 gap-2" data-testid="am-po-counts">
        {COUNTS.map((c) => (
          <div key={c.status} className="min-w-0 rounded-lg bg-bg-base px-2.5 py-2" data-testid="am-po-count" data-status={c.status}>
            <dt className="text-[11px] text-text-muted">{t(c.key)}</dt>
            <dd className="text-sm font-semibold tabular-nums text-text-primary sm:text-base">{t("po.invoices", { n: formatInt(num(summary[c.n])) })}</dd>
            {hasValue(summary[c.value]) ? (
              <dd className="truncate text-[11px] tabular-nums text-text-secondary">{t("po.baht", { baht: formatMoney2(summary[c.value]) })}</dd>
            ) : null}
          </div>
        ))}
      </dl>
      <div>
        <p className="text-xs text-text-secondary" data-testid="am-po-depth">
          {depth}
        </p>
        {twoWay > 0 ? (
          <p className="mt-0.5 text-xs text-status-warning" data-testid="am-po-two-way">
            {t("po.twoWayNote")}
          </p>
        ) : null}
      </div>
      {reasons.length ? (
        <section aria-label={t("po.reasons")} data-testid="am-po-reasons">
          <h3 className="text-xs font-medium text-text-primary">{t("po.reasons")}</h3>
          <ul className="mt-1 flex flex-col gap-0.5">
            {reasons.map((r, i) => (
              <li key={`${r.code ?? ""}-${i}`} className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs" data-testid="am-po-reason">
                <span className="min-w-0 text-text-primary">{personText(r.label ?? "")}</span>
                <span className="shrink-0 tabular-nums text-text-secondary">
                  {hasValue(r.value)
                    ? t("po.reasonLine", { n: formatInt(num(r.invoices)), baht: formatMoney2(r.value) })
                    : t("po.invoices", { n: formatInt(num(r.invoices)) })}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <p className="text-xs text-text-secondary" data-testid="am-po-no-approval">
        {t("po.noApproval")}
      </p>
    </div>
  );
}

/** "ยืนยันการรับของ (ไม่บังคับ)": folded; the lines are read from the round's computed table when it is opened. */
export function ReceiptOffers({ runId }: { runId: string }) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  const [offers, setOffers] = useState<ReceiptOffer[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const bodyId = useId();

  useEffect(() => {
    if (!open || offers) return;
    let alive = true;
    setLoadError(null);
    amApi.computed(runId).then(
      (c) => alive && setOffers(c?.receipt_offers ?? []),
      (err) => alive && setLoadError(errorMessage(err)),
    );
    return () => {
      alive = false;
    };
  }, [open, offers, runId]);

  return (
    <section className="mt-3 rounded-lg border border-border-subtle px-3 py-2" data-testid="am-receipts">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
        className="focus-ring inline-flex w-full items-center gap-1.5 rounded-md py-0.5 text-left text-sm font-medium text-text-primary"
      >
        <ChevronDown width={14} height={14} aria-hidden className={cls("shrink-0 transition-transform", open ? "rotate-180" : "")} />
        <span>{t("receipt.title")}</span>
      </button>
      {open ? (
        <div id={bodyId} className="mt-1.5">
          <p className="text-xs text-text-secondary">{t("receipt.body")}</p>
          {loadError ? (
            <p role="alert" className="mt-2 text-xs text-status-danger">
              {loadError}
            </p>
          ) : !offers ? (
            <div className="mt-2 flex items-center gap-2 text-xs text-text-muted">
              <Spinner size={12} />
              <span>{t("common.loading")}</span>
            </div>
          ) : offers.length === 0 ? (
            <p className="mt-2 text-xs text-text-muted">{t("receipt.none")}</p>
          ) : (
            <ul className="mt-2 flex flex-col divide-y divide-border-subtle">
              {offers.map((o) => (
                <ReceiptRow key={o.key} runId={runId} offer={o} locked={done} onDone={() => setDone(true)} />
              ))}
            </ul>
          )}
          {done ? (
            <p role="status" className="mt-2 text-xs text-status-success" data-testid="am-receipt-done">
              {t("receipt.done")}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

const QUANTITY = /^\d+(\.\d+)?$/;

function ReceiptRow({ runId, offer, locked, onDone }: { runId: string; offer: ReceiptOffer; locked: boolean; onDone: () => void }) {
  const t = useAmT();
  const [partial, setPartial] = useState(false);
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qtyId = useId();

  const confirm = (quantity: string) => {
    setBusy(true);
    setError(null);
    amApi.confirmReceipt(runId, offer.key, quantity).then(
      () => {
        setBusy(false);
        onDone();
      },
      (err) => {
        setBusy(false);
        setError(errorMessage(err));
      },
    );
  };
  const confirmPartial = () => {
    const q = qty.replace(/[,\s]/g, "");
    if (!QUANTITY.test(q) || Number(q) <= 0) {
      setError(t("receipt.badQuantity"));
      return;
    }
    confirm(q);
  };

  const where = offer.line_no !== null && offer.line_no !== undefined && offer.line_no !== ""
    ? t("receipt.line", { po: offer.po, line: String(offer.line_no) })
    : t("receipt.lineNoNumber", { po: offer.po });
  const what = [offer.item_code, offer.description].filter(Boolean).join(" · ");
  return (
    <li className="flex flex-col gap-1.5 py-2" data-testid="am-receipt-row" data-key={offer.key}>
      <div className="min-w-0 text-sm">
        <div className="text-text-primary">{where}</div>
        {what ? <div className="break-words text-xs text-text-secondary">{what}</div> : null}
        {offer.ordered !== null && offer.ordered !== undefined ? (
          <div className="text-[11px] tabular-nums text-text-muted">{t("receipt.ordered", { qty: String(offer.ordered), unit: offer.unit ?? "" }).trim()}</div>
        ) : null}
      </div>
      {!locked ? (
        partial ? (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!busy) confirmPartial();
            }}
          >
            <label htmlFor={qtyId} className="sr-only">
              {t("receipt.quantity")}
            </label>
            <input
              id={qtyId}
              autoFocus
              value={qty}
              inputMode="decimal"
              onChange={(e) => setQty(e.target.value)}
              placeholder={t("receipt.quantity")}
              className="focus-ring w-32 rounded-lg border border-border bg-bg-base px-3 py-1.5 text-sm tabular-nums text-text-primary placeholder:text-text-muted"
            />
            {offer.unit ? <span className="text-xs text-text-secondary">{offer.unit}</span> : null}
            <Button type="submit" size="sm" loading={busy}>
              <span>{t("receipt.confirm")}</span>
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => setPartial(false)} disabled={busy}>
              {t("common.cancel")}
            </Button>
          </form>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => confirm("all")} loading={busy}>
              <span>{t("receipt.all")}</span>
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => setPartial(true)} disabled={busy}>
              {t("receipt.partial")}
            </Button>
          </div>
        )
      ) : null}
      {error ? (
        <p role="alert" className="text-xs text-status-danger">
          {error}
        </p>
      ) : null}
    </li>
  );
}
