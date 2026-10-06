// Display formatting for Agent Market screens. Money arrives as decimal
// strings from the scripts that computed it; these helpers only FORMAT them
// (Intl accepts a numeric string exactly) and never do arithmetic on them.

import type { AmT } from "./i18n";

const NUMERIC = /^[+-]?\d+(\.\d+)?$/;

// An amount is written the same way in Thai and in English — 1,284,500.00, a
// percentage 10.5% (GLOSSARY.md §4) — so one set of formatters serves both
// languages; only dates and the words around a number follow the language.
const money = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const signedOneDecimal = new Intl.NumberFormat("th-TH", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});
const credits = new Intl.NumberFormat("th-TH", { maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat("th-TH", { maximumFractionDigits: 0 });

function numeric(v: unknown): string | null {
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  if (typeof v === "string" && NUMERIC.test(v.trim())) return v.trim();
  return null;
}

function fmt(f: Intl.NumberFormat, v: string): string {
  // Intl.NumberFormat v3 formats a decimal string without converting it to a
  // float first; the cast is for the older lib typings.
  return f.format(v as unknown as number);
}

export const DASH = "—";

/** 1284500.00 → "1,284,500" (th-TH, whole baht). Non-numbers pass through. */
export function formatMoney(v: unknown): string {
  if (v === null || v === undefined || v === "") return DASH;
  const n = numeric(v);
  return n === null ? String(v) : fmt(money, n);
}

const money2 = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** "1500.00" → "1,500.00" (a limit, an amount to the satang). Non-numbers pass through. */
export function formatMoney2(v: unknown): string {
  if (v === null || v === undefined || v === "") return DASH;
  const n = numeric(v);
  return n === null ? String(v) : fmt(money2, n);
}

/** "8.20" → "+8.2%". */
export function formatSignedPct(v: unknown): string {
  const n = numeric(v);
  return n === null ? DASH : `${fmt(signedOneDecimal, n)}%`;
}

/** "26.60" → "26.6%". */
export function formatPct(v: unknown): string {
  const n = numeric(v);
  return n === null ? DASH : `${fmt(oneDecimal, n)}%`;
}

/** "-2.00" → "−2.0" (percentage points). */
export function formatSignedNumber(v: unknown): string {
  const n = numeric(v);
  return n === null ? DASH : fmt(signedOneDecimal, n);
}

export function formatCredits(v: unknown): string {
  const n = numeric(v);
  return n === null ? "0" : fmt(credits, n);
}

export function formatInt(v: unknown): string {
  const n = numeric(v);
  return n === null ? String(v ?? "") : fmt(integer, n);
}

/** The sign of a decimal string without converting it: 1, -1 or 0. */
export function signOf(v: unknown): number {
  const n = numeric(v);
  if (n === null) return 0;
  if (/^[+]?0*(\.0*)?$/.test(n) || /^-0*(\.0*)?$/.test(n)) return 0;
  return n.startsWith("-") ? -1 : 1;
}

/** 72_000 → "1 นาที 12 วินาที" / "1 min 12 s" (units from the feature dictionary, in the language of `t`). */
export function formatDuration(ms: number | null | undefined, t: AmT): string {
  const total = Math.max(0, Math.floor((ms ?? 0) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const parts: string[] = [];
  if (h) parts.push(t("time.hours", { n: String(h) }));
  if (m) parts.push(t("time.minutes", { n: String(m) }));
  if (!h && (s || parts.length === 0)) parts.push(t("time.seconds", { n: String(s) }));
  return parts.join(" ");
}

// AM11 — a date follows the language (docs/agent-market/GLOSSARY.md §4):
// Thai is th-TH's own (Buddhist era: "28 ก.ย. 2569"); English is written
// "28 Sep 2026" and "28 Sep 2026, 10:05". The English one is put together
// from the parts rather than left to en-GB's own pattern, whose short
// September is "Sept" in today's browsers — every other month has three
// letters there, and so has this one here.
function englishDate(d: Date, withTime: boolean): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" } : {}),
  }).formatToParts(d);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
  const date = `${part("day")} ${part("month").slice(0, 3)} ${part("year")}`;
  return withTime ? `${date}, ${part("hour")}:${part("minute")}` : date;
}

export function formatDateTime(iso: string | null | undefined, locale: string): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  if (locale !== "th") return englishDate(d, true);
  return new Intl.DateTimeFormat("th-TH", { dateStyle: "medium", timeStyle: "short" }).format(d);
}

export function formatDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  if (locale !== "th") return englishDate(d, false);
  return new Intl.DateTimeFormat("th-TH", { day: "numeric", month: "short", year: "numeric" }).format(d);
}

/** Milliseconds between two ISO times (or until now when `end` is empty). */
export function elapsedBetween(start: string | null | undefined, end: string | null | undefined, now = Date.now()): number {
  if (!start) return 0;
  const a = Date.parse(start);
  if (Number.isNaN(a)) return 0;
  const b = end ? Date.parse(end) : now;
  return Number.isNaN(b) ? 0 : Math.max(0, b - a);
}

export function extensionOf(format: string): string {
  return format.startsWith(".") ? format : `.${format}`;
}

// ── AM3: what a run costs, in baht ───────────────────────────────────────

const baht = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Credits → baht with the platform's rates (3,000,000 credits = 1 USD). */
export function creditsToBaht(credits: number | null | undefined, pricing: { credits_per_usd: number; thb_per_usd: number }): number | null {
  if (credits === null || credits === undefined || !Number.isFinite(credits) || pricing.credits_per_usd <= 0) return null;
  return (credits / pricing.credits_per_usd) * pricing.thb_per_usd;
}

/** 0.4123 → "0.41"; below one satang → "< 0.01". */
export function formatBaht(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  if (v > 0 && v < 0.01) return "< 0.01";
  return baht.format(v);
}

/** AM11 — what an agent is called where there is room for a few words (the sidebar, a workspace's
 *  greeting). On a Thai screen its short name, else its name. A package has no short name in
 *  English: on an English screen it is the English name the package gives (`name_en`), else the name
 *  the caller holds — the catalogue's, which the server already sends in the screen's language, or an
 *  install's own title. */
export function agentShortName(
  m: { name?: string; name_en?: string; short_name?: string } | null | undefined,
  locale: string,
  held?: string | null,
): string {
  if (locale !== "th") return m?.name_en || held || m?.name || "";
  return m?.short_name || m?.name || held || "";
}
