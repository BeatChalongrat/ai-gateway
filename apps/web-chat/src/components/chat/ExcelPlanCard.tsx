"use client";

// Phase 4 — Excel Smart Workspace. Lifecycle:
//
//   1. AI message contains a :::excel-plan ... ::: fenced JSON block.
//      MessageBubble parses + renders <ExcelPlanCard plan={...}/>.
//   2. User clicks Execute Plan → POST /excel/execute. The card
//      flips into a loading state, then into <ExcelResultCard/>.
//   3. ResultCard shows the generated file (download), the change
//      log, and the validator report.
//
// Both cards share the visual frame (rounded panel, subtle border)
// so the transition between "plan" → "result" feels in-place rather
// than a swap to a different component.

import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { ExcelExecuteResponse, FileAttachment } from "@/lib/types";

interface ExcelPlanAction {
  type?: string;
  sheet_name?: string;
  [k: string]: unknown;
}

export interface ExcelPlan {
  operation?: string;
  source_file_id?: string;
  output_filename?: string;
  description?: string;
  actions?: ExcelPlanAction[];
  [k: string]: unknown;
}

interface Props {
  convId: string;
  plan: ExcelPlan;
}

export function ExcelPlanCard({ convId, plan }: Props) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ExcelExecuteResponse | null>(null);

  async function execute() {
    if (status === "running") return;
    setStatus("running");
    setError(null);
    try {
      const srcId = plan.source_file_id;
      if (!srcId) {
        throw new Error("plan is missing source_file_id");
      }
      const res = await api.executeExcelPlan(convId, {
        source_file_id: srcId,
        output_filename: plan.output_filename,
        plan,
      });
      setResult(res);
      setStatus("done");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message;
      setError(msg || "Could not execute plan.");
      setStatus("error");
    }
  }

  if (status === "done" && result) {
    return <ExcelResultCard result={result} />;
  }

  const summary = summarizeActions(plan.actions);

  return (
    <div className="my-3 rounded-xl border border-border-subtle bg-bg-surface p-5 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <span aria-hidden className="text-base">📊</span>
        <span className="text-sm font-semibold text-text-primary">Excel Edit Plan</span>
      </div>
      {plan.description ? (
        <p className="mb-3 text-sm italic text-text-secondary">
          &ldquo;{plan.description}&rdquo;
        </p>
      ) : null}
      {summary.length > 0 ? (
        <div className="mb-4">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Changes
          </div>
          <ul className="space-y-1 text-sm text-text-primary">
            {summary.map((line, i) => (
              <li key={i} className="flex gap-2">
                <span aria-hidden className="text-text-tertiary">•</span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={execute}
          disabled={status === "running"}
          className="focus-ring tap-44 inline-flex items-center gap-2 rounded-lg bg-text-primary px-4 py-2 text-sm font-medium text-bg-base transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {status === "running" ? (
            <>
              <Spinner />
              <span>Processing…</span>
            </>
          ) : (
            <>
              <span aria-hidden>▶</span>
              <span>Execute Plan</span>
            </>
          )}
        </button>
        {error ? (
          <span className="text-xs text-status-danger" role="alert">{error}</span>
        ) : null}
      </div>
    </div>
  );
}

export function ExcelResultCard({ result }: { result: ExcelExecuteResponse }) {
  const { file, validation, changes } = result;
  const sizeLabel = humanSize(file.file_size_bytes);
  const sheets = validation?.stats?.sheets;
  const charts = validation?.stats?.charts;
  const broken = validation?.stats?.broken_formulas ?? 0;

  return (
    <div className="my-3 rounded-xl border border-border-subtle bg-bg-surface p-5 shadow-sm">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span aria-hidden className="text-base">✅</span>
          <span className="truncate text-sm font-semibold text-text-primary">
            {file.filename}
          </span>
        </div>
        <DownloadButton fileId={file.id} filename={file.filename} />
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-tertiary">
        {file.version ? <span>Version {file.version}</span> : null}
        <span>{sizeLabel}</span>
        {sheets != null ? <span>{sheets} sheet{sheets === 1 ? "" : "s"}</span> : null}
        {charts != null ? <span>{charts} chart{charts === 1 ? "" : "s"}</span> : null}
      </div>
      {changes && changes.length > 0 ? (
        <div className="mb-3">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Changes made
          </div>
          <ul className="space-y-1 text-sm text-text-primary">
            {changes.map((line, i) => (
              <li key={i} className="flex gap-2">
                <span aria-hidden className={line.startsWith("ERROR") ? "text-status-danger" : "text-status-success"}>
                  {line.startsWith("ERROR") ? "✗" : "✓"}
                </span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <ValidationBadge validation={validation} brokenCount={broken} />
    </div>
  );
}

function ValidationBadge({
  validation,
  brokenCount,
}: {
  validation: ExcelExecuteResponse["validation"];
  brokenCount: number;
}) {
  if (!validation) return null;
  if (validation.valid && brokenCount === 0 && validation.warnings.length === 0) {
    return (
      <div className="rounded-md border border-border-subtle bg-bg-base px-3 py-2 text-xs text-status-success">
        ✅ Validation: File is valid, no broken formulas.
      </div>
    );
  }
  if (validation.valid) {
    return (
      <div className="rounded-md border border-border-subtle bg-bg-base px-3 py-2 text-xs text-text-secondary">
        ⚠️ Validation passed with warnings:
        <ul className="mt-1 space-y-0.5 pl-4 list-disc">
          {validation.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      </div>
    );
  }
  return (
    <div className="rounded-md border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-status-danger">
      ❌ Validation failed:
      <ul className="mt-1 space-y-0.5 pl-4 list-disc">
        {validation.issues.map((m, i) => (
          <li key={i}>{m}</li>
        ))}
      </ul>
    </div>
  );
}

function DownloadButton({ fileId, filename }: { fileId: string; filename: string }) {
  const href = api.downloadUrl(fileId);
  return (
    <a
      href={href}
      download={filename}
      className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border-subtle px-3 py-1.5 text-xs font-medium text-text-primary transition-colors hover:bg-bg-elevated"
    >
      <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="7 10 12 15 17 10" />
        <line x1="12" y1="15" x2="12" y2="3" />
      </svg>
      Download
    </a>
  );
}

function Spinner() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className="animate-spin"
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

function humanSize(bytes: number): string {
  if (!bytes) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

// summarizeActions renders a short English-readable bullet per
// plan action. Mostly mirrors excel_executor.py's change-log lines
// so the user sees the same wording before and after execution.
function summarizeActions(actions?: ExcelPlanAction[]): string[] {
  if (!actions || actions.length === 0) return [];
  const lines: string[] = [];
  for (const a of actions) {
    const sheet = typeof a.sheet_name === "string" ? a.sheet_name : undefined;
    switch (a.type) {
      case "create_sheet":
        lines.push(`Create sheet "${sheet ?? "?"}"`);
        break;
      case "delete_sheet":
        lines.push(`Delete sheet "${sheet ?? "?"}"`);
        break;
      case "rename_sheet":
        lines.push(`Rename "${sheet ?? "?"}" → "${(a as { new_name?: string }).new_name ?? "?"}"`);
        break;
      case "write_cells": {
        const cells = (a as { cells?: unknown[] }).cells;
        const n = Array.isArray(cells) ? cells.length : 0;
        lines.push(`Write ${n} cell${n === 1 ? "" : "s"} in "${sheet ?? "?"}"`);
        break;
      }
      case "write_table":
        lines.push(`Write table in "${sheet ?? "?"}"`);
        break;
      case "create_chart": {
        const title = (a as { title?: string }).title || "chart";
        const kind = (a as { chart_type?: string }).chart_type || "bar";
        lines.push(`Create ${kind} chart "${title}" in "${sheet ?? "?"}"`);
        break;
      }
      case "format_range":
        lines.push(`Format range in "${sheet ?? "?"}"`);
        break;
      case "set_column_width":
        lines.push(`Set column widths in "${sheet ?? "?"}"`);
        break;
      case "merge_cells":
        lines.push(`Merge cells in "${sheet ?? "?"}"`);
        break;
      case "auto_filter":
        lines.push(`Add filter in "${sheet ?? "?"}"`);
        break;
      case "freeze_panes":
        lines.push(`Freeze panes in "${sheet ?? "?"}"`);
        break;
      default:
        lines.push(`${a.type || "action"} in "${sheet ?? "?"}"`);
    }
  }
  return lines;
}
