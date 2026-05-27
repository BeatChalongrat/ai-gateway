"use client";

// Phase 4 Session 2 — Word/PDF Smart Report Generation.
//
// Sibling of ExcelPlanCard. Lifecycle is identical:
//
//   1. AI message contains a :::doc-plan ... ::: fenced JSON block.
//      MessageBubble parses + renders <DocPlanCard plan={...}/>.
//   2. User clicks Generate Document → POST /document/execute.
//      The card flips to loading, then to <DocResultCard/>.
//   3. ResultCard shows the generated file (download), change log,
//      validation, and version / size / page-count badges.
//
// Visual frame matches the Excel cards so the chat surface reads
// as one consistent system regardless of which generator fired.

import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { DocExecuteResponse } from "@/lib/types";

interface DocAction {
  type?: string;
  text?: string;
  level?: number;
  headers?: unknown[];
  rows?: unknown[];
  items?: unknown[];
  [k: string]: unknown;
}

export interface DocPlan {
  format?: "docx" | "pdf";
  source_file_id?: string;
  output_filename?: string;
  document_settings?: {
    page_size?: string;
    orientation?: string;
    language?: string;
    title_page?: { title?: string; subtitle?: string };
    [k: string]: unknown;
  };
  actions?: DocAction[];
  [k: string]: unknown;
}

interface Props {
  convId: string;
  plan: DocPlan;
}

export function DocPlanCard({ convId, plan }: Props) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DocExecuteResponse | null>(null);

  async function execute() {
    if (status === "running") return;
    setStatus("running");
    setError(null);
    try {
      const format = (plan.format || "docx") as "docx" | "pdf";
      const res = await api.executeDocumentPlan(convId, {
        source_file_id: plan.source_file_id || undefined,
        output_filename: plan.output_filename,
        format,
        plan,
      });
      setResult(res);
      setStatus("done");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message;
      setError(msg || "Could not generate document.");
      setStatus("error");
    }
  }

  if (status === "done" && result) {
    return <DocResultCard result={result} />;
  }

  const format = (plan.format || "docx").toUpperCase();
  const settings = plan.document_settings || {};
  const language = (settings.language as string) || "en";
  const orientation = (settings.orientation as string) || "portrait";
  const pageSize = (settings.page_size as string) || "A4";
  const summary = summarizeDocActions(plan.actions);

  return (
    <div className="my-3 rounded-xl border border-border-subtle bg-bg-surface p-5 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <span aria-hidden className="text-base">📄</span>
        <span className="text-sm font-semibold text-text-primary">
          {format === "PDF" ? "PDF Report Plan" : "Document Report Plan"}
        </span>
      </div>
      <div className="mb-3 text-xs text-text-tertiary">
        {format === "PDF" ? "PDF" : "Word (.docx)"} · {pageSize} {orientation} · {language.toUpperCase()}
        {plan.source_file_id ? " · editing source" : " · from scratch"}
      </div>
      {settings.title_page?.title ? (
        <p className="mb-3 text-sm italic text-text-secondary">
          &ldquo;{settings.title_page.title}&rdquo;
        </p>
      ) : null}
      {summary.length > 0 ? (
        <div className="mb-4">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Structure
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
              <span>Generating…</span>
            </>
          ) : (
            <>
              <span aria-hidden>🔨</span>
              <span>Generate Document</span>
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

export function DocResultCard({ result }: { result: DocExecuteResponse }) {
  const { file, validation, generation, changes } = result;
  const sizeLabel = humanSize(file.file_size_bytes);
  const pages = generation?.page_count ?? validation?.stats?.page_count;
  const words = validation?.stats?.word_count;
  const fileTypeLabel = (file.content_type || "").includes("pdf") ? "PDF" : "DOCX";

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
        <span>{fileTypeLabel}</span>
        {file.version ? <span>Version {file.version}</span> : null}
        <span>{sizeLabel}</span>
        {pages != null ? <span>{pages} page{pages === 1 ? "" : "s"}</span> : null}
        {words != null ? <span>{words} word{words === 1 ? "" : "s"}</span> : null}
      </div>
      {changes && changes.length > 0 ? (
        <details className="mb-3">
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Change log ({changes.length})
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {changes.map((line, i) => {
              const broken = line.startsWith("ERROR");
              return (
                <li key={i} className="flex gap-2">
                  <span aria-hidden className={broken ? "text-status-danger" : "text-status-success"}>
                    {broken ? "✗" : "✓"}
                  </span>
                  <span>{line}</span>
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}
      <ValidationBadge validation={validation} />
    </div>
  );
}

function ValidationBadge({ validation }: { validation: DocExecuteResponse["validation"] }) {
  if (!validation) return null;
  if (validation.valid && validation.warnings.length === 0) {
    return (
      <div className="rounded-md border border-border-subtle bg-bg-base px-3 py-2 text-xs text-status-success">
        ✅ Validation: File is valid.
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
        {validation.errors.map((m, i) => (
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

// summarizeDocActions turns the plan's flat action list into a
// short bullet list — same idea as ExcelPlanCard's summarizer but
// keyed off the doc action types.
function summarizeDocActions(actions?: DocAction[]): string[] {
  if (!actions || actions.length === 0) return [];
  const lines: string[] = [];
  for (const a of actions) {
    switch (a.type) {
      case "add_title_page":
        lines.push("Title page");
        break;
      case "add_heading": {
        const lvl = typeof a.level === "number" ? a.level : 1;
        lines.push(`H${lvl}: ${truncate((a.text as string) || "", 60)}`);
        break;
      }
      case "add_paragraph": {
        const text = (a.text as string) || "";
        lines.push(`Paragraph: ${truncate(text, 60)}`);
        break;
      }
      case "add_bullet_list": {
        const n = Array.isArray(a.items) ? a.items.length : 0;
        lines.push(`Bullet list (${n} item${n === 1 ? "" : "s"})`);
        break;
      }
      case "add_numbered_list": {
        const n = Array.isArray(a.items) ? a.items.length : 0;
        lines.push(`Numbered list (${n} item${n === 1 ? "" : "s"})`);
        break;
      }
      case "add_table": {
        const cols = Array.isArray(a.headers) ? a.headers.length : 0;
        const rows = Array.isArray(a.rows) ? a.rows.length : 0;
        lines.push(`Table (${cols} cols × ${rows} rows)`);
        break;
      }
      case "add_page_break":
        lines.push("Page break");
        break;
      case "add_horizontal_rule":
        lines.push("Horizontal rule");
        break;
      case "add_spacer":
        lines.push("Spacer");
        break;
      case "add_blockquote": {
        const text = (a.text as string) || "";
        lines.push(`Blockquote: ${truncate(text, 50)}`);
        break;
      }
      case "add_code_block":
        lines.push("Code block");
        break;
      case "replace_text": {
        const find = (a.find as string) || "";
        const repl = (a.replace as string) || "";
        lines.push(`Replace "${truncate(find, 24)}" → "${truncate(repl, 24)}"`);
        break;
      }
      case "set_header":
        lines.push(`Set header: ${truncate((a.text as string) || "", 50)}`);
        break;
      case "set_footer":
        lines.push(`Set footer: ${truncate((a.text as string) || "", 50)}`);
        break;
      default:
        lines.push(a.type || "action");
    }
  }
  return lines;
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
