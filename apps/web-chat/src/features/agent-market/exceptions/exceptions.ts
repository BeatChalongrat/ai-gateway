// A run's exceptions (AM2 phase F, agent_run_exceptions): what the package
// left for a person to decide. "review" rows are open until a person picks
// one of the row's own choices; "info" rows are stored as "noted" — shown,
// waiting on nobody. There is no reject: a choice is always one the package
// offered. Pure helpers for the panel and the dashboard.

import type { AmKey } from "../i18n";
import type { ExceptionSourceRef, RunExceptionRow } from "../types";

export interface ExceptionGroups {
  open: RunExceptionRow[];
  resolved: RunExceptionRow[];
  noted: RunExceptionRow[];
}

export function groupExceptions(rows: RunExceptionRow[] | null | undefined): ExceptionGroups {
  const out: ExceptionGroups = { open: [], resolved: [], noted: [] };
  for (const r of rows ?? []) {
    if (r.status === "open") out.open.push(r);
    else if (r.status === "resolved") out.resolved.push(r);
    else out.noted.push(r);
  }
  return out;
}

// The expense audit's reason codes (expense_audit/scripts/audit.py REASON_TH), labelled in the dictionary.
const REASON_CODES = new Set([
  "claim_row_unreadable",
  "unknown_category",
  "over_claim_limit",
  "over_period_limit",
  "missing_receipt",
  "receipt_amount_mismatch",
  "receipt_unconfirmed",
  "duplicate_claim",
  "split_purchase",
  "prohibited_merchant",
  "vat_claim_without_full_tax_invoice",
  "wht_missing",
  "wht_mismatch",
  "missing_purpose",
  "weekend_or_holiday",
  "vague_purpose",
  "receipt_not_linked",
]);

/** The dictionary key of a reason code's Thai label, or null when the feature has none (show the code). */
export function reasonLabelKey(code: string): AmKey | null {
  return REASON_CODES.has(code) ? (`reason.${code}` as AmKey) : null;
}

export interface EvidencePart {
  key: AmKey;
  vars?: Record<string, string>;
}

const str = (v: unknown) => (v === null || v === undefined ? "" : String(v).trim());

/** Where the finding was read: sheet row and cell, receipt and page, the printed text, the table's limit. */
export function evidenceParts(ref: ExceptionSourceRef | null | undefined, money: (v: string) => string): EvidencePart[] {
  if (!ref) return [];
  const out: EvidencePart[] = [];
  const row = str(ref.row);
  const sheet = str(ref.sheet_name);
  if (row) out.push(sheet ? { key: "exq.sheetRow", vars: { sheet, row } } : { key: "exq.row", vars: { row } });
  if (str(ref.cell)) out.push({ key: "exq.cell", vars: { cell: str(ref.cell) } });
  if (str(ref.receipt)) out.push({ key: "exq.receipt", vars: { name: str(ref.receipt) } });
  if (str(ref.page)) out.push({ key: "exq.page", vars: { n: str(ref.page) } });
  if (str(ref.raw_text)) out.push({ key: "exq.raw", vars: { text: str(ref.raw_text).replace(/\s+/g, " ") } });
  if (str(ref.limit)) {
    const amount = money(str(ref.limit));
    out.push(ref.limit_source === "policy_table" ? { key: "exq.limitTable", vars: { amount } } : { key: "exq.limit", vars: { amount } });
  }
  return out;
}

/** A row's reason in words: the package's own Thai sentence, else the feature's label — never the
 *  code (AM5: a code is the engine's name for the reason, not a person's). */
export function reasonText(row: Pick<RunExceptionRow, "reason_code" | "source_ref">, label: (k: AmKey) => string): string {
  const own = str(row.source_ref?.reason);
  if (own) return own;
  const k = reasonLabelKey(row.reason_code);
  return label(k ?? "reason.unknown");
}
