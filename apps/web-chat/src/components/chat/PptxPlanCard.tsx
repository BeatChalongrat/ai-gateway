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
  RotateCw,
  Table2,
  Target,
} from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { OfficeQualityNotice } from "./OfficeQualityNotice";
import { api } from "@/lib/api";
import { tOutside, useT } from "@/lib/i18n";
import {
  persistedPlanFailure,
  planFailureFromError,
  type PlanFailure,
} from "@/lib/planFailure";
import type {
  DocExecuteResponse,
  FileAttachment,
  PlanOutcome,
  PptxGenerationReport,
} from "@/lib/types";

import {
  CardShell,
  DownloadButton,
  OpenPreviewButton,
  MetadataRow,
  PlanFailureNotice,
  PlanList,
  PlanListItem,
  PrimaryButton,
  ProvenanceLine,
  ValidationBadge,
  humanSize,
  linkedGeneratedFile,
  serverMessageId,
  truncate,
  useConversationFilename,
} from "./planCardUI";
import type { DocResult } from "./DocPlanCard";

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
  actions?: { type: string; slide?: number; to?: number; find?: string; replace?: string; text?: string }[];
  [k: string]: unknown;
}

// Monochrome slide-type icons — colour was removed in Session 5.1
// so the deck outline reads as structural metadata rather than
// a coloured status alarm. Icon SHAPE alone carries the slide-
// type hint.
const SLIDE_ICON_CLASS = "h-4 w-4 text-text-muted";
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
  /** FIX126 F4 — the assistant message that wrote the plan, and its files. */
  messageId?: string;
  files?: FileAttachment[];
  /** FIX131 — the message's failed plan executions (GET /messages). */
  outcomes?: PlanOutcome[];
}

export function PptxPlanCard({ convId, plan, messageId, files, outcomes }: Props) {
  const t = useT();
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">(
    "idle",
  );
  const [failure, setFailure] = useState<PlanFailure | null>(null);
  const [result, setResult] = useState<DocExecuteResponse | null>(null);
  const sourceName = useConversationFilename(convId, plan.source_file_id);

  async function execute() {
    if (status === "running") return;
    setStatus("running");
    setFailure(null);
    try {
      const res = await api.executeDocumentPlan(convId, {
        source_file_id: plan.source_file_id || undefined,
        output_filename: plan.output_filename,
        format: "pptx",
        plan,
        message_id: serverMessageId(messageId),
      });
      setResult(res);
      setStatus("done");
    } catch (err) {
      setFailure(planFailureFromError(err)); // FIX131
      setStatus("error");
    }
  }

  if (status === "done" && result) {
    return <PptxResultCard result={result} />;
  }
  // FIX126 F4 — the deck this plan produced, read back after a reload.
  const linked =
    status === "idle" ? linkedGeneratedFile(files, ["pptx"], plan.output_filename) : undefined;
  if (linked) {
    return <PptxResultCard result={{ file: linked, changes: linked.change_log ?? [] }} />;
  }
  // FIX131 — a failure kept on the message survives the reload.
  const shownFailure =
    status === "error"
      ? failure
      : status === "idle"
        ? persistedPlanFailure(outcomes, "pptx-plan", plan.output_filename)
        : undefined;

  const settings = plan.presentation_settings || {};
  const layout = (settings.layout as string) || "16:9";
  const language = ((settings.language as string) || "en").toUpperCase();
  const slides = plan.slides || [];
  const actions = plan.actions || [];
  const metadata = [
    ...(!plan.source_file_id || settings.layout ? [layout] : []),
    ...(!plan.source_file_id || settings.language ? [language] : []),
    ...(slides.length || !actions.length ? [`${slides.length} ${t(slides.length === 1 ? "unit.slide" : "unit.slides")}`] : []),
    ...(actions.length ? [`${actions.length} ${t(actions.length === 1 ? "unit.action" : "unit.actions")}`] : []),
  ].join(" · ");

  return (
    <CardShell
      headerIcon={<FileTypeBadge extension=".pptx" size="sm" />}
      headerTitle={t("plan.pptx.title")}
      metadata={metadata}
      footer={
        <PrimaryButton
          onClick={execute}
          loading={status === "running"}
          icon={shownFailure ? <RotateCw className="h-4 w-4" /> : undefined}
        >
          {status === "running"
            ? t("plan.generating")
            : shownFailure
              ? t("plan.retry")
              : t("plan.pptx.generate")}
        </PrimaryButton>
      }
    >
      {shownFailure ? <PlanFailureNotice failure={shownFailure} kind="PowerPoint" /> : null}
      <ProvenanceLine source={sourceName ?? shownFailure?.sourceFilename} />
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
      {actions.length > 0 ? (
        <PlanList title={t("plan.changes")}>
          {actions.map((action, i) => (
            <PlanListItem key={i} icon={<FileText className={SLIDE_ICON_CLASS} />} index={i + 1}>
              {summarizeEdit(action)}
            </PlanListItem>
          ))}
        </PlanList>
      ) : null}
    </CardShell>
  );
}

function summarizeEdit(action: NonNullable<PptxPlan["actions"]>[number]): string {
  switch (action.type) {
    case "replace_text": return tOutside("plan.pptx.replaceText", { find: action.find ?? "", replace: action.replace ?? "" });
    case "set_notes": return tOutside("plan.pptx.setNotes", { slide: String(action.slide ?? "") });
    case "move_slide": return tOutside("plan.pptx.moveSlide", { slide: String(action.slide ?? ""), to: String(action.to ?? "") });
    case "delete_slide": return tOutside("plan.pptx.deleteSlide", { slide: String(action.slide ?? "") });
    default: return tOutside("plan.changes");
  }
}

export function PptxResultCard({ result }: { result: DocResult }) {
  const t = useT();
  const { file, validation, generation, changes } = result;
  const pptxGen = generation as unknown as PptxGenerationReport | undefined;
  const slideCount = pptxGen?.slide_count ?? validation?.stats?.page_count;
  const wordCount = validation?.stats?.word_count;
  const slideLog = pptxGen?.change_log;
  const madeFromInput = file.made_from?.role === "input"; // FIX131

  return (
    <CardShell
      headerIcon={<FileTypeBadge extension=".pptx" size="sm" />}
      headerTitle={file.filename}
      metadata={file.version && !madeFromInput ? `v${file.version}` : undefined}
      footer={
        <>
          <OpenPreviewButton file={file} />
          <DownloadButton fileId={file.id} filename={file.filename} />
        </>
      }
    >
      <ProvenanceLine
        source={file.made_from?.filename}
        output={file.filename}
        role={file.made_from?.role}
      />
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
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("plan.slideLog", { n: String(slideLog.length) })}
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {slideLog.map((entry, i) => (
              <li key={i} className="flex items-start gap-2">
                <span
                  className={`mt-0.5 flex-shrink-0 ${
                    entry.status === "error"
                      ? "text-status-danger"
                      : "text-text-muted"
                  }`}
                >
                  {entry.status === "error" ? "✗" : "✓"}
                </span>
                <span>
                  {entry.slide != null ? t("plan.slideEntry", {
                    n: String(entry.slide),
                    type: entry.type,
                  }) : `${entry.action ?? i + 1}. ${t("plan.changes")}`}
                  {entry.detail ? ` — ${entry.detail}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : changes && changes.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("plan.changeLog", { n: String(changes.length) })}
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-text-primary">
            {changes.map((line, i) => {
              const broken = line.startsWith("ERROR");
              return (
                <li key={i} className="flex items-start gap-2">
                  <span
                    className={`mt-0.5 flex-shrink-0 ${
                      broken ? "text-status-danger" : "text-text-muted"
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
      <OfficeQualityNotice report={validation?.office_quality} />
      {validation ? (
        <ValidationBadge
          valid={validation.valid ?? false}
          warnings={validation.warnings ?? []}
          errors={validation.errors ?? []}
          okMessage={t("plan.validation.okPresentation")}
        />
      ) : null}
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
