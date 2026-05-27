"use client";

// Phase 4 Session 2 — Word/PDF Smart Report Generation card pair.
// Polished in Session 4 with Lucide icons via the shared planCardUI
// shell (matches the Excel + Pptx cards 1:1 for visual consistency).

import { useState, type ReactNode } from "react";

import {
  AlignLeft,
  Heading,
  Image as ImageIcon,
  List,
  ListOrdered,
  Minus,
  PanelBottom,
  PanelTop,
  Presentation,
  Quote,
  Replace,
  SeparatorHorizontal,
  Table2,
  Type,
} from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { api, ApiError } from "@/lib/api";
import type { DocExecuteResponse } from "@/lib/types";

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

// Monochrome icon set — colour was removed in Session 5.1 so the
// plan list reads as structural metadata rather than a coloured
// status alarm. Icon SHAPE alone carries the action-type hint.
const ACTION_ICON_CLASS = "h-4 w-4 text-text-tertiary";
const ACTION_ICONS: Record<string, ReactNode> = {
  add_title_page:      <Presentation className={ACTION_ICON_CLASS} />,
  add_heading:         <Heading className={ACTION_ICON_CLASS} />,
  add_paragraph:       <AlignLeft className={ACTION_ICON_CLASS} />,
  add_bullet_list:     <List className={ACTION_ICON_CLASS} />,
  add_numbered_list:   <ListOrdered className={ACTION_ICON_CLASS} />,
  add_table:           <Table2 className={ACTION_ICON_CLASS} />,
  add_page_break:      <SeparatorHorizontal className={ACTION_ICON_CLASS} />,
  add_spacer:          <SeparatorHorizontal className={ACTION_ICON_CLASS} />,
  add_horizontal_rule: <Minus className={ACTION_ICON_CLASS} />,
  add_blockquote:      <Quote className={ACTION_ICON_CLASS} />,
  add_code_block:      <Type className={ACTION_ICON_CLASS} />,
  replace_text:        <Replace className={ACTION_ICON_CLASS} />,
  set_header:          <PanelTop className={ACTION_ICON_CLASS} />,
  set_footer:          <PanelBottom className={ACTION_ICON_CLASS} />,
};

function actionIcon(type?: string): ReactNode {
  if (type && ACTION_ICONS[type]) return ACTION_ICONS[type];
  return <ImageIcon className={ACTION_ICON_CLASS} />;
}

interface Props {
  convId: string;
  plan: DocPlan;
}

export function DocPlanCard({ convId, plan }: Props) {
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
  const language = ((settings.language as string) || "en").toUpperCase();
  const orientation = (settings.orientation as string) || "portrait";
  const pageSize = (settings.page_size as string) || "A4";
  const actions = plan.actions ?? [];

  const formatLabel = format === "PDF" ? "PDF Report" : "Word Document";

  return (
    <CardShell
      headerIcon={<FileTypeBadge extension={format === "PDF" ? ".pdf" : ".docx"} size="sm" />}
      headerTitle={`${formatLabel} Plan`}
      metadata={`${pageSize} ${orientation} · ${language} · ${actions.length} action${actions.length === 1 ? "" : "s"}`}
      footer={
        <>
          <PrimaryButton onClick={execute} loading={status === "running"}>
            {status === "running" ? "Generating…" : "Generate Document"}
          </PrimaryButton>
          {error ? <ErrorLine message={error} /> : null}
        </>
      }
    >
      {settings.title_page?.title ? (
        <p className="text-sm italic text-text-secondary">
          &ldquo;{settings.title_page.title}&rdquo;
        </p>
      ) : null}
      {actions.length > 0 ? (
        <PlanList title="Structure">
          {actions.map((a, i) => (
            <PlanListItem key={i} icon={actionIcon(a.type)}>
              {summarizeDocAction(a)}
            </PlanListItem>
          ))}
        </PlanList>
      ) : null}
    </CardShell>
  );
}

export function DocResultCard({ result }: { result: DocExecuteResponse }) {
  const { file, validation, changes } = result;
  const fileTypeLabel = (file.content_type || "").includes("pdf") ? "PDF" : "DOCX";
  const headerIcon = (
    <FileTypeBadge extension={fileTypeLabel === "PDF" ? ".pdf" : ".docx"} size="sm" />
  );
  const pages = file.version != null
    ? validation?.stats?.page_count
    : validation?.stats?.page_count;
  const words = validation?.stats?.word_count;

  return (
    <CardShell
      headerIcon={headerIcon}
      headerTitle={file.filename}
      metadata={file.version ? `v${file.version}` : undefined}
      footer={
        <DownloadButton fileId={file.id} filename={file.filename} />
      }
    >
      <MetadataRow>
        <span>{fileTypeLabel}</span>
        <span>{humanSize(file.file_size_bytes)}</span>
        {pages != null ? (
          <span>{pages} page{pages === 1 ? "" : "s"}</span>
        ) : null}
        {words != null ? (
          <span>{words} word{words === 1 ? "" : "s"}</span>
        ) : null}
      </MetadataRow>
      {changes && changes.length > 0 ? (
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
      />
    </CardShell>
  );
}

function summarizeDocAction(a: DocAction): string {
  switch (a.type) {
    case "add_title_page":
      return "Title page";
    case "add_heading": {
      const lvl = typeof a.level === "number" ? a.level : 1;
      return `H${lvl}: ${truncate((a.text as string) || "", 60)}`;
    }
    case "add_paragraph":
      return `Paragraph: ${truncate((a.text as string) || "", 60)}`;
    case "add_bullet_list": {
      const n = Array.isArray(a.items) ? a.items.length : 0;
      return `Bullet list (${n} item${n === 1 ? "" : "s"})`;
    }
    case "add_numbered_list": {
      const n = Array.isArray(a.items) ? a.items.length : 0;
      return `Numbered list (${n} item${n === 1 ? "" : "s"})`;
    }
    case "add_table": {
      const cols = Array.isArray(a.headers) ? a.headers.length : 0;
      const rows = Array.isArray(a.rows) ? a.rows.length : 0;
      return `Table (${cols} cols × ${rows} rows)`;
    }
    case "add_page_break":
      return "Page break";
    case "add_horizontal_rule":
      return "Horizontal rule";
    case "add_spacer":
      return "Spacer";
    case "add_blockquote":
      return `Blockquote: ${truncate((a.text as string) || "", 50)}`;
    case "add_code_block":
      return "Code block";
    case "replace_text": {
      const find = (a.find as string) || "";
      const repl = (a.replace as string) || "";
      return `Replace "${truncate(find, 24)}" → "${truncate(repl, 24)}"`;
    }
    case "set_header":
      return `Set header: ${truncate((a.text as string) || "", 50)}`;
    case "set_footer":
      return `Set footer: ${truncate((a.text as string) || "", 50)}`;
    default:
      return a.type || "action";
  }
}
