"use client";

// A question's evidence, kept generic: the checks that raise questions send
// free-form JSON. Two shapes are recognised —
//   * a table: `rows` (array of arrays) with optional `columns`, or
//     `sample_rows` with optional `sample_columns`;
//   * everything else scalar-ish (file, count, debit_total, months{…},
//     proposal{field: header}, values[…]) as a key/value list.
// Collapsed by default behind "ดูตัวอย่าง N แถว" / "ดูหลักฐาน".

import { useId, useState } from "react";
import { ChevronDown } from "lucide-react";

import { useAmT, type AmKey } from "../i18n";
import { cls } from "../ui";

type Row = unknown[];

interface Parsed {
  table: { columns: string[] | null; rows: Row[] } | null;
  facts: { key: string; value: string }[];
}

const TABLE_KEYS = new Set(["rows", "columns", "sample_rows", "sample_columns"]);
const MAX_ROWS = 50;

function isRowArray(v: unknown): v is Row[] {
  return Array.isArray(v) && v.length > 0 && v.every((r) => Array.isArray(r));
}

function isStringArray(v: unknown): v is string[] {
  return Array.isArray(v) && v.every((x) => typeof x === "string" || typeof x === "number");
}

function scalar(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(scalar).filter(Boolean).join(", ");
  if (typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k}: ${scalar(x)}`)
      .join(" · ");
  }
  return "";
}

export function parseEvidence(evidence: unknown): Parsed {
  const out: Parsed = { table: null, facts: [] };
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) return out;
  const ev = evidence as Record<string, unknown>;
  if (isRowArray(ev.rows)) {
    out.table = { columns: isStringArray(ev.columns) ? ev.columns.map(String) : null, rows: ev.rows };
  } else if (isRowArray(ev.sample_rows)) {
    out.table = { columns: isStringArray(ev.sample_columns) ? ev.sample_columns.map(String) : null, rows: ev.sample_rows };
  }
  for (const [key, value] of Object.entries(ev)) {
    if (out.table && TABLE_KEYS.has(key)) continue; // shown as the table
    if (isRowArray(value)) continue; // a second table: not flattened into a line
    // Without a table, `rows` next to `columns` is a COUNT (a wrong-file
    // finding) and both read as plain facts.
    const text = scalar(value);
    if (text) out.facts.push({ key, value: text });
  }
  return out;
}

const KNOWN_KEYS = new Set([
  "file", "sheet", "header_row", "count", "debit_total", "months", "proposal", "headers", "column", "values",
  "detected", "error", "currencies", "accounts", "sample", "columns", "rows",
]);

export function Evidence({ evidence }: { evidence: unknown }) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const { table, facts } = parseEvidence(evidence);
  if (!table && facts.length === 0) return null;
  const label = table ? t("evidence.rows", { n: String(table.rows.length) }) : t("evidence.show");
  const rows = table ? table.rows.slice(0, MAX_ROWS) : [];
  const width = table ? Math.max(table.columns?.length ?? 0, ...rows.map((r) => r.length)) : 0;

  return (
    <div className="mt-1.5">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="focus-ring inline-flex items-center gap-1 rounded-md py-0.5 text-xs text-text-secondary hover:text-text-primary"
        data-testid="evidence-toggle"
      >
        <ChevronDown width={14} height={14} aria-hidden className={cls("transition-transform", open ? "rotate-180" : "")} />
        <span>{label}</span>
      </button>
      {open ? (
        <div id={panelId} className="mt-1.5 flex flex-col gap-2" data-testid="evidence-panel">
          {facts.length ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-lg bg-bg-base px-3 py-2 text-xs">
              {facts.map((f) => (
                <div key={f.key} className="contents">
                  <dt className="text-text-muted">{KNOWN_KEYS.has(f.key) ? t(`ev.${f.key}` as AmKey) : f.key}</dt>
                  <dd className="min-w-0 break-words text-text-primary">{f.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {table ? (
            <div className="scrollbar-thin max-h-72 overflow-auto rounded-lg border border-border-subtle">
              <table className="w-full border-collapse text-left text-xs tabular-nums">
                {table.columns ? (
                  <thead className="sticky top-0 bg-bg-elevated">
                    <tr>
                      {Array.from({ length: width }, (_, i) => (
                        <th key={i} scope="col" className="whitespace-nowrap px-2.5 py-1.5 font-medium text-text-secondary">
                          {table.columns?.[i] ?? ""}
                        </th>
                      ))}
                    </tr>
                  </thead>
                ) : null}
                <tbody>
                  {rows.map((r, ri) => (
                    <tr key={ri} className="border-t border-border-subtle first:border-t-0">
                      {Array.from({ length: width }, (_, ci) => (
                        <td key={ci} className="max-w-[16rem] truncate whitespace-nowrap px-2.5 py-1.5 text-text-primary" title={scalar(r[ci])}>
                          {scalar(r[ci])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
