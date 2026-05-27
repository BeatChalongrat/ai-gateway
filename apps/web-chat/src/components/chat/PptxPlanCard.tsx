"use client";

// Phase 4 Session 3 — PowerPoint Smart Presentation Generation.
//
// Sibling of DocPlanCard / ExcelPlanCard. Lifecycle is identical:
//
//   1. AI message contains a :::pptx-plan ... ::: fenced JSON block.
//      MessageBubble parses + renders <PptxPlanCard plan={...}/>.
//   2. User clicks Generate Presentation → POST /document/execute
//      with format=pptx. The card flips to loading, then to
//      <PptxResultCard/>.
//   3. ResultCard shows the generated deck (download), the per-
//      slide log, and the validator report.

import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import type {
  DocExecuteResponse,
  PptxChangeLogEntry,
  PptxGenerationReport,
} from "@/lib/types";

interface PptxSlide {
  type?: string;
  title?: string;
  subtitle?: string;
  body?: string;
  bullets?: unknown[];
  left?: { heading?: string; bullets?: unknown[] };
  right?: { heading?: string; bullets?: unknown[] };
  stats?: unknown[];
  headers?: unknown[];
  rows?: unknown[];
  chart_type?: string;
  data?: { categories?: unknown[]; series?: unknown[] };
  [k: string]: unknown;
}

export interface PptxPlan {
  format?: "pptx";
  source_file_id?: string;
  output_filename?: string;
  presentation_settings?: {
    layout?: string;
    language?: string;
    title?: string;
    [k: string]: unknown;
  };
  slides?: PptxSlide[];
  [k: string]: unknown;
}

interface Props {
  convId: string;
  plan: PptxPlan;
}

export function PptxPlanCard({ convId, plan }: Props) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DocExecuteResponse | null>(null);

  async function execute() {
    if (status === "running") return;
    setStatus("running");
    setError(null);
    try {
      const res = await api.executeDocumentPlan(convId, {
        source_file_id: plan.source_file_id || undefined,
        output_filename: plan.output_filename,
        format: "pptx",
        plan,
      });
      setResult(res);
      setStatus("done");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err as Error).message;
      setError(msg || "Could not generate presentation.");
      setStatus("error");
    }
  }

  if (status === "done" && result) {
    return <PptxResultCard result={result} />;
  }

  const settings = plan.presentation_settings || {};
  const layout = (settings.layout as string) || "16:9";
  const language = ((settings.language as string) || "en").toUpperCase();
  const slides = plan.slides || [];

  return (
    <div className="my-3 rounded-xl border border-border-subtle bg-bg-surface p-5 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <span aria-hidden className="text-base">📊</span>
        <span className="text-sm font-semibold text-text-primary">
          Presentation Plan
        </span>
      </div>
      <div className="mb-3 text-xs text-text-tertiary">
        {layout} · {slides.length} slide{slides.length === 1 ? "" : "s"} · {language}
        {plan.source_file_id ? " · editing source" : " · from scratch"}
      </div>
      {settings.title ? (
        <p className="mb-3 text-sm italic text-text-secondary">
          &ldquo;{settings.title as string}&rdquo;
        </p>
      ) : null}
      {slides.length > 0 ? (
        <div className="mb-4">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Slides
          </div>
          <ol className="space-y-1 text-sm text-text-primary">
            {slides.map((slide, i) => (
              <li key={i} className="flex gap-2">
                <span aria-hidden className="text-text-tertiary">{i + 1}.</span>
                <span aria-hidden className="shrink-0">{slideIcon(slide.type)}</span>
                <span className="truncate">{summarizeSlide(slide)}</span>
              </li>
            ))}
          </ol>
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
              <span>Generate Presentation</span>
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

export function PptxResultCard({ result }: { result: DocExecuteResponse }) {
  // The generation envelope is structurally the same as the
  // doc result but with slide-keyed entries; treat it loosely so a
  // future generator addition doesn't break the card.
  const { file, validation, generation, changes } = result;
  const pptxGen = generation as unknown as PptxGenerationReport | undefined;
  const slideCount = pptxGen?.slide_count ?? validation?.stats?.page_count;
  const wordCount = validation?.stats?.word_count;
  const slideLog = pptxGen?.change_log;

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
        <span>PPTX</span>
        {file.version ? <span>Version {file.version}</span> : null}
        <span>{humanSize(file.file_size_bytes)}</span>
        {slideCount != null ? (
          <span>
            {slideCount} slide{slideCount === 1 ? "" : "s"}
          </span>
        ) : null}
        {wordCount != null ? (
          <span>
            {wordCount} word{wordCount === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>
      {slideLog && slideLog.length > 0 ? (
        <details className="mb-3" open={slideLog.length <= 8}>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Slide log ({slideLog.length})
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {slideLog.map((entry, i) => (
              <li key={i} className="flex gap-2">
                <span
                  aria-hidden
                  className={
                    entry.status === "error"
                      ? "text-status-danger"
                      : "text-status-success"
                  }
                >
                  {entry.status === "error" ? "✗" : "✓"}
                </span>
                <span>
                  Slide {entry.slide}: {entry.type}
                  {entry.detail ? ` — ${entry.detail}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : changes && changes.length > 0 ? (
        <details className="mb-3">
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Change log ({changes.length})
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {changes.map((line, i) => {
              const broken = line.startsWith("ERROR");
              return (
                <li key={i} className="flex gap-2">
                  <span
                    aria-hidden
                    className={broken ? "text-status-danger" : "text-status-success"}
                  >
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

function ValidationBadge({
  validation,
}: {
  validation: DocExecuteResponse["validation"];
}) {
  if (!validation) return null;
  if (validation.valid && validation.warnings.length === 0) {
    return (
      <div className="rounded-md border border-border-subtle bg-bg-base px-3 py-2 text-xs text-status-success">
        ✅ Validation: Presentation is valid.
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

// slideIcon and summarizeSlide turn the plan's slide entries into
// human-readable bullets for the Plan card. Matches the icon set in
// the spec but stays text-only (no external icon dep).
function slideIcon(type?: string): string {
  switch (type) {
    case "title": return "🎬";
    case "section_divider": return "📑";
    case "two_column": return "↔️";
    case "stats": return "⚡";
    case "table": return "📋";
    case "chart": return "📊";
    case "content_with_image": return "🖼️";
    case "closing": return "🎯";
    case "content":
    default: return "📝";
  }
}

function summarizeSlide(slide: PptxSlide): string {
  const t = slide.type || "content";
  const title = (slide.title as string) || "";
  switch (t) {
    case "title":
      return `Title — "${truncate(title, 60)}"`;
    case "section_divider":
      return `Section — "${truncate(title, 60)}"`;
    case "two_column":
      return `Two-column — "${truncate(title, 50)}"`;
    case "stats": {
      const n = Array.isArray(slide.stats) ? slide.stats.length : 0;
      return `Stats — ${n} KPI${n === 1 ? "" : "s"}`;
    }
    case "table": {
      const cols = Array.isArray(slide.headers) ? slide.headers.length : 0;
      const rows = Array.isArray(slide.rows) ? slide.rows.length : 0;
      return `Table — "${truncate(title, 40)}" (${cols} cols × ${rows} rows)`;
    }
    case "chart": {
      const cats = slide.data?.categories?.length ?? 0;
      const series = slide.data?.series?.length ?? 0;
      const kind = slide.chart_type || "bar";
      return `Chart — "${truncate(title, 40)}" (${kind}, ${cats} cats × ${series} series)`;
    }
    case "closing":
      return `Closing — "${truncate(title || "Thank You", 60)}"`;
    case "content_with_image":
    case "content":
    default: {
      const n = Array.isArray(slide.bullets) ? slide.bullets.length : 0;
      const tag = n > 0 ? ` (${n} bullet${n === 1 ? "" : "s"})` : "";
      return `Content — "${truncate(title, 50)}"${tag}`;
    }
  }
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
