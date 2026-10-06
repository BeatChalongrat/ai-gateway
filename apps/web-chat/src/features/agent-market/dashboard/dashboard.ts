// The per-agent dashboard (AM2 phase G, GET /installs/{id}/dashboard):
// the helpers that turn its numbers into words and bar widths.

import { DASH } from "../format";
import type { AmKey, AmT } from "../i18n";
import type { AgentDashboard, DashCount } from "../types";

export const RANGES = [7, 30, 90] as const;

/** 95 → "1 นาที 35 วินาที", 90061 → "1 วัน 1 ชั่วโมง": the two largest units; null → "—". */
export function formatSeconds(s: number | null | undefined, t: AmT): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return DASH;
  const total = Math.max(0, Math.round(s));
  const units: [number, AmKey][] = [
    [86_400, "time.days"],
    [3_600, "time.hours"],
    [60, "time.minutes"],
    [1, "time.seconds"],
  ];
  const parts: string[] = [];
  let rest = total;
  for (const [size, key] of units) {
    const n = Math.floor(rest / size);
    if (n > 0 || (size === 1 && parts.length === 0)) {
      parts.push(t(key, { n: String(n) }));
      rest -= n * size;
    } else if (parts.length) {
      break; // units must be adjacent: "1 วัน 5 นาที" would read wrong
    }
    if (parts.length === 2) break;
  }
  return parts.join(" ");
}

/** 0.7234 → "72%"; null → "—". */
export function formatRate(r: number | null | undefined): string {
  return r === null || r === undefined || !Number.isFinite(r) ? DASH : `${Math.round(r * 100)}%`;
}

export interface Bar {
  key: string;
  count: number;
  open: number;
  /** width against the largest count, 0-100 */
  pct: number;
  /** AM11: the row in words, when the server says them */
  label?: string;
}

export function bars(rows: DashCount[] | null | undefined): Bar[] {
  const list = (rows ?? []).filter((r) => r && r.count > 0);
  const max = Math.max(1, ...list.map((r) => r.count));
  return list.map((r) => ({ key: r.key, count: r.count, open: r.open ?? 0, pct: Math.round((r.count / max) * 100), ...(r.label ? { label: r.label } : {}) }));
}

/** A {key: count} record as bars, in a fixed order (keys not in it follow). */
export function recordBars(rec: Record<string, number> | null | undefined, order: readonly string[]): Bar[] {
  const r = rec ?? {};
  const keys = [...order.filter((k) => k in r), ...Object.keys(r).filter((k) => !order.includes(k))];
  return bars(keys.map((key) => ({ key, count: r[key] ?? 0 })));
}

export const CONFIDENCE_ORDER = ["high", "medium", "low", "person", "computed"] as const;
export const TIER_ORDER = ["digital", "etax", "scan", "photo"] as const;

/** Nothing has happened in the window and nothing is waiting: a new (or idle) agent. */
export function isEmpty(d: AgentDashboard): boolean {
  return d.runs.total === 0 && d.waiting.runs === 0 && d.waiting.open_exceptions === 0 && d.exceptions.open + d.exceptions.resolved + d.exceptions.noted === 0;
}
