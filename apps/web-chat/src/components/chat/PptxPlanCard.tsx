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

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { api, ApiError } from "@/lib/api";
import { tOutside, useT } from "@/lib/i18n";
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

// Monochrome slide-type icons — colour was removed in Session 5.1
// so the deck outline reads as structural metadata rather than
// a coloured status alarm. Icon SHAPE alone carries the slide-
// type hint.
const SLIDE_ICON_CLASS = "h-4 w-4 text-text-tertiary";
const SLIDE_ICONS: Record<string, ReactNode> = {
  title:              <Presentation className={SLIDE_ICON_CLASS} />,
  section_divider:    <LayoutTemplate className={SLIDE_ICON_CLASS} />,
  content:            <FileText className={SLIDE_ICON_CLASS} />,
  content_with_image: <ImageIcon className={SLIDE_ICON_CLASS} />,
  two_column:         <Columns2 className={SLIDE_ICON_CLASS} />,
  stats:              <Hash className={SLIDE_ICON_CLASS} />,
  table:              <Table2 className={SLIDE_ICON_CLASS} />,
  chart:              <BarChart3 className={SLIDE_ICON_CLASS} />,
  closing:            <Target className={SLIDE_ICON_CLASS} />,
};

function slideIcon(type?: string): ReactNode {
  if (type && SLIDE_ICONS[type]) return SLIDE_ICONS[type];
  return <FileText className={SLIDE_ICON_CLASS} />;
}

interface Props {
  convId: string;
  plan: PptxPlan;
}

export function PptxPlanCard({ convId, plan }: Props) {
  const t = useT();
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
      setError(msg || tOutside("plan.pptx.execFailed"));
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
      headerIcon={<FileTypeBadge extension=".pptx" size="sm" />}
      headerTitle={t("plan.pptx.title")}
      metadata={`${layout} · ${language} · ${slides.length} ${t(slides.length === 1 ? "unit.slide" : "unit.slides")}`}
      footer={
        <>
          <PrimaryButton onClick={execute} loading={status === "running"}>
            {status === "running" ? t("plan.generating") : t("plan.pptx.generate")}
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
        <PlanList title={t("plan.slides")}>
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
  const t = useT();
  const { file, validation, generation, changes } = result;
  const pptxGen = generation as unknown as PptxGenerationReport | undefined;
  const slideCount = pptxGen?.slide_count ?? validation?.stats?.page_count;
  const wordCount = validation?.stats?.word_count;
  const slideLog = pptxGen?.change_log;

  return (
    <CardShell
      headerIcon={<FileTypeBadge extension=".pptx" size="sm" />}
      headerTitle={file.filename}
      metadata={file.version ? `v${file.version}` : undefined}
      footer={<DownloadButton fileId={file.id} filename={file.filename} />}
    >
      <MetadataRow>
        <span>PPTX</span>
        <span>{humanSize(file.file_size_bytes)}</span>
        {slideCount != null ? (
          <span>{slideCount} {t(slideCount === 1 ? "unit.slide" : "unit.slides")}</span>
        ) : null}
        {wordCount != null ? (
          <span>{wordCount} {t(wordCount === 1 ? "unit.word" : "unit.words")}</span>
        ) : null}
      </MetadataRow>
      {slideLog && slideLog.length > 0 ? (
        <details open={slideLog.length <= 8}>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-tertiary">
            {t("plan.slideLog", { n: String(slideLog.length) })}
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {slideLog.map((entry, i) => (
              <li key={i} className="flex items-start gap-2">
                <span
                  className={`mt-0.5 flex-shrink-0 ${
                    entry.status === "error"
                      ? "text-status-danger"
                      : "text-text-tertiary"
                  }`}
                >
                  {entry.status === "error" ? "✗" : "✓"}
                </span>
                <span>
                  {t("plan.slideEntry", {
                    n: String(entry.slide),
                    type: entry.type,
                  })}
                  {entry.detail ? ` — ${entry.detail}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : changes && changes.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-tertiary">
            {t("plan.changeLog", { n: String(changes.length) })}
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {changes.map((line, i) => {
              const broken = line.startsWith("ERROR");
              return (
                <li key={i} className="flex items-start gap-2">
                  <span
                    className={`mt-0.5 flex-shrink-0 ${
                      broken ? "text-status-danger" : "text-text-tertiary"
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
        okMessage={t("plan.validation.okPresentation")}
      />
    </CardShell>
  );
}

function summarizeSlide(slide: PptxSlide): string {
  const t = slide.type || "content";
  const title = (slide.title as string) || "";
  switch (t) {
    case "title":
      return tOutside("plan.pptx.slideTitle", { title: truncate(title, 60) });
    case "section_divider":
      return tOutside("plan.pptx.section", { title: truncate(title, 60) });
    case "two_column":
      return tOutside("plan.pptx.twoColumn", { title: truncate(title, 50) });
    case "stats": {
      const n = Array.isArray(slide.stats) ? slide.stats.length : 0;
      return n === 1
        ? tOutside("plan.pptx.stats.one")
        : tOutside("plan.pptx.stats.other", { n: String(n) });
    }
    case "table": {
      const cols = Array.isArray(slide.headers) ? slide.headers.length : 0;
      const rows = Array.isArray(slide.rows) ? slide.rows.length : 0;
      return tOutside("plan.pptx.table", {
        title: truncate(title, 40),
        cols: String(cols),
        rows: String(rows),
      });
    }
    case "chart": {
      const cats = slide.data?.categories?.length ?? 0;
      const series = slide.data?.series?.length ?? 0;
      const kind = slide.chart_type || "bar";
      return tOutside("plan.pptx.chart", {
        title: truncate(title, 40),
        kind,
        cats: String(cats),
        series: String(series),
      });
    }
    case "closing":
      return tOutside("plan.pptx.closing", {
        title: truncate(title || tOutside("plan.pptx.thankYou"), 60),
      });
    case "content_with_image":
    case "content":
    default: {
      const n = Array.isArray(slide.bullets) ? slide.bullets.length : 0;
      const vars = { title: truncate(title, 50), n: String(n) };
      if (n === 0) return tOutside("plan.pptx.content.none", vars);
      return n === 1
        ? tOutside("plan.pptx.content.one", vars)
        : tOutside("plan.pptx.content.other", vars);
    }
  }
}
