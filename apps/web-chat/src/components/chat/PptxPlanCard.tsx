"use client";

// Phase 4 Session 3 — PowerPoint card pair, polished in Session 4
// with Lucide icons via the shared planCardUI shell.

import { useState, type ReactNode } from "react";

import {
  BarChart3,
  Columns2,
  FileText,
  Hash,
  Image as ImageIcon,
  LayoutTemplate,
  Presentation,
  Table2,
  Target,
} from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type {
  DocExecuteResponse,
  PptxGenerationReport,
} from "@/lib/types";

import {
  CardShell,
  DownloadButton,
  ErrorLine,
  MetadataRow,
  PlanList,
  PlanListItem,
  PrimaryButton,
  ValidationBadge,
  humanSize,
  truncate,
} from "./planCardUI";

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

// Per-slide-type icons. Colour coded by intent so a long deck
// reads as a glance — blue for structural slides, emerald for
// data, amber for KPIs, purple for two-column, slate for content,
// rose for the closing.
const SLIDE_ICONS: Record<string, ReactNode> = {
  title:              <Presentation className="h-4 w-4 text-blue-500" />,
  section_divider:    <LayoutTemplate className="h-4 w-4 text-indigo-500" />,
  content:            <FileText className="h-4 w-4 text-slate-500" />,
  content_with_image: <ImageIcon className="h-4 w-4 text-cyan-500" />,
  two_column:         <Columns2 className="h-4 w-4 text-purple-500" />,
  stats:              <Hash className="h-4 w-4 text-amber-500" />,
  table:              <Table2 className="h-4 w-4 text-slate-500" />,
  chart:              <BarChart3 className="h-4 w-4 text-emerald-500" />,
  closing:            <Target className="h-4 w-4 text-rose-500" />,
};

function slideIcon(type?: string): ReactNode {
  if (type && SLIDE_ICONS[type]) return SLIDE_ICONS[type];
  return <FileText className="h-4 w-4 text-text-tertiary" />;
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
    <CardShell
      headerIcon={<Presentation className="h-5 w-5 text-amber-500" />}
      headerTitle="Presentation Plan"
      metadata={`${layout} · ${language} · ${slides.length} slide${slides.length === 1 ? "" : "s"}`}
      footer={
        <>
          <PrimaryButton onClick={execute} loading={status === "running"}>
            {status === "running" ? "Generating…" : "Generate Presentation"}
          </PrimaryButton>
          {error ? <ErrorLine message={error} /> : null}
        </>
      }
    >
      {settings.title ? (
        <p className="text-sm italic text-text-secondary">
          &ldquo;{settings.title as string}&rdquo;
        </p>
      ) : null}
      {slides.length > 0 ? (
        <PlanList title="Slides">
          {slides.map((slide, i) => (
            <PlanListItem key={i} icon={slideIcon(slide.type)} index={i + 1}>
              {summarizeSlide(slide)}
            </PlanListItem>
          ))}
        </PlanList>
      ) : null}
    </CardShell>
  );
}

export function PptxResultCard({ result }: { result: DocExecuteResponse }) {
  const { file, validation, generation, changes } = result;
  const pptxGen = generation as unknown as PptxGenerationReport | undefined;
  const slideCount = pptxGen?.slide_count ?? validation?.stats?.page_count;
  const wordCount = validation?.stats?.word_count;
  const slideLog = pptxGen?.change_log;

  return (
    <CardShell
      headerIcon={<Presentation className="h-5 w-5 text-amber-500" />}
      headerTitle={file.filename}
      metadata={file.version ? `v${file.version}` : undefined}
      footer={<DownloadButton fileId={file.id} filename={file.filename} />}
    >
      <MetadataRow>
        <span>PPTX</span>
        <span>{humanSize(file.file_size_bytes)}</span>
        {slideCount != null ? (
          <span>{slideCount} slide{slideCount === 1 ? "" : "s"}</span>
        ) : null}
        {wordCount != null ? (
          <span>{wordCount} word{wordCount === 1 ? "" : "s"}</span>
        ) : null}
      </MetadataRow>
      {slideLog && slideLog.length > 0 ? (
        <details open={slideLog.length <= 8}>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Slide log ({slideLog.length})
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {slideLog.map((entry, i) => (
              <li key={i} className="flex items-start gap-2">
                <span
                  className={`mt-0.5 flex-shrink-0 ${
                    entry.status === "error"
                      ? "text-status-danger"
                      : "text-emerald-500"
                  }`}
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
        <details>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-tertiary">
            Change log ({changes.length})
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {changes.map((line, i) => {
              const broken = line.startsWith("ERROR");
              return (
                <li key={i} className="flex items-start gap-2">
                  <span
                    className={`mt-0.5 flex-shrink-0 ${
                      broken ? "text-status-danger" : "text-emerald-500"
                    }`}
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
      <ValidationBadge
        valid={validation?.valid ?? false}
        warnings={validation?.warnings ?? []}
        errors={validation?.errors ?? []}
        okMessage="Presentation is valid."
      />
    </CardShell>
  );
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
