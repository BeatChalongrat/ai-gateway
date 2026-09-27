// The expense policy table (AM2 phase F, install setting "policy", schema
// am2.expense_policy.v1) and the holiday calendar (setting "holidays"),
// as the editor holds them and as they are saved.
//
// Every limit the expense audit uses comes from this table only; a policy
// document sent with a run is quoted as context and never becomes a limit.
// The checks here are the server's (agentmarket/expense_policy.go) and the
// package's (expense_audit/checks/policy.py load_policy), run before sending
// so a person hears what is wrong at once; the server checks again.

import type { AmKey } from "../i18n";
import { arabicDigits } from "../typed";
import type { ExpensePolicy, PolicyCategory } from "../types";

export const POLICY_SCHEMA = "am2.expense_policy.v1";
export const WEEKEND_RULES = ["flag", "exception", "allow"] as const;
export const VAT_DOCUMENTS = ["full_tax_invoice", "abbreviated_tax_invoice"] as const;
export type WeekendRule = (typeof WEEKEND_RULES)[number];
export type VatDocument = (typeof VAT_DOCUMENTS)[number];

const CODE_RX = /^[A-Za-z0-9_-]+$/;
const MONEY_RX = /^\d+(?:\.\d+)?$/;

export interface CategoryDraft {
  /** local row id (React key, problem keys) — never saved */
  id: string;
  code: string;
  label: string;
  per_claim: string;
  per_person_month: string;
  needs_purpose: boolean;
  wht_rate: string;
  wht_from: string;
  /** keys this editor does not show, kept as they were */
  extra: Record<string, unknown>;
}

export interface PolicyDraft {
  source: string;
  receipt_required_above: string;
  approval_threshold: string;
  categories: CategoryDraft[];
  /** one word per line */
  prohibited: string;
  weekend_holiday: WeekendRule;
  vat_claim_requires: VatDocument;
  extra: Record<string, unknown>;
}

export interface HolidayRow {
  id: string;
  date: string;
  name: string;
}

let seq = 0;
export const rowId = (prefix: string) => `${prefix}-${++seq}`;

const text = (v: unknown): string => (v === null || v === undefined ? "" : typeof v === "string" ? v : typeof v === "number" ? String(v) : "");

const CATEGORY_KEYS = new Set(["label", "per_claim", "per_person_month", "needs_purpose", "wht_rate", "wht_from"]);
const POLICY_KEYS = new Set([
  "schema",
  "source",
  "currency",
  "receipt_required_above",
  "approval_threshold",
  "categories",
  "prohibited_merchants",
  "weekend_holiday",
  "vat_claim_requires",
]);

function rest(o: Record<string, unknown>, known: Set<string>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(o).filter(([k]) => !known.has(k)));
}

export function emptyCategory(): CategoryDraft {
  return { id: rowId("cat"), code: "", label: "", per_claim: "", per_person_month: "", needs_purpose: false, wht_rate: "", wht_from: "", extra: {} };
}

/** A stored table (or the manifest's sample) → what the editor holds. */
export function draftFromPolicy(p: ExpensePolicy | null | undefined): PolicyDraft {
  const cats = p?.categories && typeof p.categories === "object" ? p.categories : {};
  return {
    source: text(p?.source) || "install",
    receipt_required_above: text(p?.receipt_required_above),
    approval_threshold: text(p?.approval_threshold),
    categories: Object.entries(cats).map(([code, c]: [string, PolicyCategory]) => ({
      id: rowId("cat"),
      code,
      label: text(c?.label),
      per_claim: text(c?.per_claim),
      per_person_month: text(c?.per_person_month),
      needs_purpose: c?.needs_purpose === true,
      wht_rate: text(c?.wht_rate),
      wht_from: text(c?.wht_from),
      extra: c && typeof c === "object" ? rest(c, CATEGORY_KEYS) : {},
    })),
    prohibited: Array.isArray(p?.prohibited_merchants) ? p!.prohibited_merchants!.map(String).join("\n") : "",
    weekend_holiday: (WEEKEND_RULES as readonly string[]).includes(String(p?.weekend_holiday)) ? (p!.weekend_holiday as WeekendRule) : "flag",
    vat_claim_requires: (VAT_DOCUMENTS as readonly string[]).includes(String(p?.vat_claim_requires))
      ? (p!.vat_claim_requires as VatDocument)
      : "full_tax_invoice",
    extra: p ? rest(p, POLICY_KEYS) : {},
  };
}

/** A plain amount as typed (commas, Thai digits, บาท/฿ allowed) → its digits, or null ("ประมาณ 2,000" is not one). */
export function plainAmount(v: string): string | null {
  const t = arabicDigits(v).replace(/,/g, "").replace(/บาท|฿/g, "").trim();
  return MONEY_RX.test(t) ? t : null;
}

/** 1500 → "1500.00", 1500.5 → "1500.50" (text only; never a float). */
export function moneyText(v: string): string {
  const [whole, frac = ""] = v.split(".");
  return frac.length > 2 ? v : `${whole}.${frac.padEnd(2, "0")}`;
}

/** A WHT rate as typed ("3", "3%", "๓") → its number text, or null when it is not a percent in (0, 100]. */
export function plainRate(v: string): string | null {
  const t = arabicDigits(v).replace(/%/g, "").trim();
  if (!MONEY_RX.test(t)) return null;
  const n = Number(t);
  return n > 0 && n <= 100 ? t : null;
}

export const splitWords = (s: string) =>
  s
    .split(/\n|,/)
    .map((w) => w.trim())
    .filter(Boolean);

/** Field → why it is wrong. Keys: the top-level field, "categories", or "cat.<row id>.<field>". */
export type PolicyProblems = Record<string, AmKey>;

export function checkPolicy(d: PolicyDraft): PolicyProblems {
  const out: PolicyProblems = {};
  const money = (key: string, v: string, required: boolean) => {
    if (!v.trim()) {
      if (required) out[key] = "policy.err.required";
    } else if (plainAmount(v) === null) out[key] = "policy.err.money";
  };
  money("receipt_required_above", d.receipt_required_above, true);
  money("approval_threshold", d.approval_threshold, true);
  if (d.categories.length === 0) out.categories = "policy.err.noCategory";
  const seen = new Set<string>();
  for (const c of d.categories) {
    const k = (f: string) => `cat.${c.id}.${f}`;
    const code = c.code.trim();
    if (!code) out[k("code")] = "policy.err.required";
    else if (!CODE_RX.test(code)) out[k("code")] = "policy.err.code";
    else if (seen.has(code)) out[k("code")] = "policy.err.codeTwice";
    seen.add(code);
    money(k("per_claim"), c.per_claim, false);
    money(k("per_person_month"), c.per_person_month, false);
    money(k("wht_from"), c.wht_from, false);
    if (c.wht_rate.trim()) {
      if (plainRate(c.wht_rate) === null) out[k("wht_rate")] = "policy.err.rate";
      else if (!c.wht_from.trim()) out[k("wht_from")] = "policy.err.whtFrom";
    }
  }
  return out;
}

/** The table to save (only valid drafts should get here: amounts are normalised, empty limits left out). */
export function policyFromDraft(d: PolicyDraft): ExpensePolicy {
  const amount = (v: string) => {
    const p = plainAmount(v);
    return p === null ? undefined : moneyText(p);
  };
  const categories: Record<string, PolicyCategory> = {};
  for (const c of d.categories) {
    const cat: PolicyCategory = { ...c.extra };
    if (c.label.trim()) cat.label = c.label.trim();
    const perClaim = amount(c.per_claim);
    if (perClaim) cat.per_claim = perClaim;
    const perMonth = amount(c.per_person_month);
    if (perMonth) cat.per_person_month = perMonth;
    if (c.needs_purpose) cat.needs_purpose = true;
    const rate = plainRate(c.wht_rate);
    if (rate) cat.wht_rate = rate;
    const from = amount(c.wht_from);
    if (from) cat.wht_from = from;
    categories[c.code.trim()] = cat;
  }
  return {
    ...d.extra,
    schema: POLICY_SCHEMA,
    source: d.source || "install",
    currency: "THB",
    receipt_required_above: amount(d.receipt_required_above),
    approval_threshold: amount(d.approval_threshold),
    categories,
    prohibited_merchants: splitWords(d.prohibited),
    weekend_holiday: d.weekend_holiday,
    vat_claim_requires: d.vat_claim_requires,
  };
}

/** Any edit makes the table the company's own (source "install"). */
export function edited(d: PolicyDraft, patch: Partial<PolicyDraft>): PolicyDraft {
  return { ...d, ...patch, source: "install" };
}

// ── the holiday calendar ─────────────────────────────────────────────────

/** Setting "holidays" ({"YYYY-MM-DD": name}, or {"dates": {...}}) → rows by date. */
export function holidayRows(v: unknown): HolidayRow[] {
  if (!v || typeof v !== "object" || Array.isArray(v)) return [];
  let cal = v as Record<string, unknown>;
  if (cal.dates && typeof cal.dates === "object" && !Array.isArray(cal.dates)) cal = cal.dates as Record<string, unknown>;
  return Object.entries(cal)
    .map(([date, name]) => ({ id: rowId("hol"), date, name: text(name) }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

/** A real calendar day written YYYY-MM-DD. */
export function isIsoDay(s: string): boolean {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  return y >= 1900 && dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d;
}

/** Row → why it is wrong. Keys "<row id>.date" / "<row id>.name". */
export function checkHolidays(rows: HolidayRow[]): Record<string, AmKey> {
  const out: Record<string, AmKey> = {};
  const seen = new Set<string>();
  for (const r of rows) {
    if (!isIsoDay(r.date)) out[`${r.id}.date`] = "policy.err.date";
    else if (seen.has(r.date)) out[`${r.id}.date`] = "policy.err.dateTwice";
    seen.add(r.date);
    if (!r.name.trim()) out[`${r.id}.name`] = "policy.err.holidayName";
  }
  return out;
}

/** The setting to save, or null for none (the package's Thai 2569 public holidays). */
export function holidaysSetting(rows: HolidayRow[]): Record<string, string> | null {
  if (!rows.length) return null;
  return Object.fromEntries(rows.map((r) => [r.date, r.name.trim()]));
}
