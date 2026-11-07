// Values a person types for an agent (AM2): a card's typed answer and the
// company's tax ID. Checked here before anything is sent, with the rules the
// package applies on the server (backend/agent_market/engine/e2/typed.py and
// thai_text.py). The server checks again — this only says what is wrong
// without a round trip. Pure functions, no React.

import type { AmKey } from "./i18n";
import type { InputKind } from "./types";

const TH_DIGITS = "๐๑๒๓๔๕๖๗๘๙";

/** Thai digits → Arabic digits (everything else untouched). */
export function arabicDigits(s: string): string {
  return s.replace(/[๐-๙]/g, (d) => String(TH_DIGITS.indexOf(d)));
}

/** A tax ID as typed (Thai digits, spaces, hyphens) → its digits. */
export function normalizeTaxId(s: string): string {
  return arabicDigits(s).replace(/[\s-]/g, "");
}

/** Thai mod-11: sum(d[i] × (13 − i)) for i < 12, then (11 − sum mod 11) mod 10 = d[12]. */
export function taxIdCheckOk(digits: string): boolean {
  if (!/^\d{13}$/.test(digits)) return false;
  let sum = 0;
  for (let i = 0; i < 12; i++) sum += Number(digits[i]) * (13 - i);
  return (11 - (sum % 11)) % 10 === Number(digits[12]);
}

/** 0105545009875 → 0-1055-45009-87-5 (the grouping printed on Thai documents). */
export function formatTaxId(digits: string): string {
  if (!/^\d{13}$/.test(digits)) return digits;
  return `${digits.slice(0, 1)}-${digits.slice(1, 5)}-${digits.slice(5, 10)}-${digits.slice(10, 12)}-${digits.slice(12)}`;
}

/** ok → the normalised value; otherwise the dictionary key of the reason. */
export type Checked = { ok: true; value: string } | { ok: false; reason: AmKey };

export function checkTaxId(text: string): Checked {
  const d = normalizeTaxId(text.trim());
  if (!d) return { ok: false, reason: "typed.err.empty" };
  if (!/^\d{13}$/.test(d)) return { ok: false, reason: "typed.err.taxIdLength" };
  if (!taxIdCheckOk(d)) return { ok: false, reason: "typed.err.taxIdCheck" };
  return { ok: true, value: d };
}

/** The branch code of a head office. */
export const HEAD_OFFICE = "00000";

/** A branch number: 5 digits, and not 00000 (that one is the head office). */
export function checkBranch(text: string): Checked {
  const d = arabicDigits(text).replace(/\s/g, "");
  if (!/^\d{5}$/.test(d)) return { ok: false, reason: "companyTax.err.branch" };
  if (d === HEAD_OFFICE) return { ok: false, reason: "companyTax.err.branchHead" };
  return { ok: true, value: d };
}

// 12,850.70 · 12850.7 · 1,234 — at most 2 decimals; "บาท" / "฿" allowed (typed.py)
const MONEY_RE = /^-?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d{1,2})?$/;
const DMY_RE = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/;
const YMD_RE = /^(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})$/;

/** A real calendar day; a year of 2400 or more is Buddhist Era. */
function realDay(year: number, month: number, day: number): boolean {
  const y = year >= 2400 ? year - 543 : year;
  const d = new Date(Date.UTC(y, month - 1, day));
  return d.getUTCFullYear() === y && d.getUTCMonth() === month - 1 && d.getUTCDate() === day;
}

function isDate(t: string): boolean {
  const dmy = DMY_RE.exec(t);
  if (dmy) return realDay(Number(dmy[3]), Number(dmy[2]), Number(dmy[1]));
  const ymd = YMD_RE.exec(t);
  if (ymd) return realDay(Number(ymd[1]), Number(ymd[2]), Number(ymd[3]));
  return false;
}

/** A card's typed value for its input kind. An unknown kind is plain text. */
export function checkTyped(kind: InputKind | string, text: string): Checked {
  const t = arabicDigits(text).trim();
  if (!t) return { ok: false, reason: "typed.err.empty" };
  switch (kind) {
    case "money": {
      const n = t.replace(/บาท|฿|\s/g, "");
      return MONEY_RE.test(n) ? { ok: true, value: n.replace(/,/g, "") } : { ok: false, reason: "typed.err.money" };
    }
    case "tax_id":
      return checkTaxId(t);
    case "date":
      return isDate(t) ? { ok: true, value: t } : { ok: false, reason: "typed.err.date" };
    case "rate": {
      const n = t.replace(/,/g, "");
      return /^\d+(?:\.\d+)?$/.test(n) && Number(n) > 0 ? { ok: true, value: n } : { ok: false, reason: "typed.err.rate" };
    }
  }
  return { ok: true, value: t };
}

const KINDS = new Set<string>(["money", "tax_id", "date", "rate", "text"]);

/** A question's `input` as the card uses it, or null when it takes none. */
export function questionInput(input: unknown): { kind: InputKind; hint: string } | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const o = input as { kind?: unknown; hint?: unknown };
  const kind = typeof o.kind === "string" && KINDS.has(o.kind) ? (o.kind as InputKind) : "text";
  return { kind, hint: typeof o.hint === "string" ? o.hint.trim() : "" };
}
