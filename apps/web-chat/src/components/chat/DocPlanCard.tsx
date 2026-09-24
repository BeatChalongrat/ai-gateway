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
  RotateCw,
  SeparatorHorizontal,
  Table2,
  Type,
} from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { api } from "@/lib/api";
import { tOutside, useT } from "@/lib/i18n";
import {
  persistedPlanFailure,
  planFailureFromError,
  type PlanFailure,
} from "@/lib/planFailure";
import type { DocExecuteResponse, FileAttachment, PlanOutcome } from "@/lib/types";

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

/** What a result card needs. A card restored from a reloaded message has
 *  the file and its change log but no validator report (FIX126 F4). */
export type DocResult = Pick<DocExecuteResponse, "file" | "changes"> &
  Partial<Pick<DocExecuteResponse, "validation" | "generation">>;

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
const ACTION_ICON_CLASS = "h-4 w-4 text-text-muted";
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
  /** FIX126 F4 — the assistant message that wrote the plan, and its files. */
  messageId?: string;
  files?: FileAttachment[];
  /** FIX131 — the message's failed plan executions (GET /messages). */
  outcomes?: PlanOutcome[];
}

export function DocPlanCard({ convId, plan, messageId, files, outcomes }: Props) {
  const t = useT();
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">(
    "idle",
  );
  const [failure, setFailure] = useState<PlanFailure | null>(null);
  const [result, setResult] = useState<DocExecuteResponse | null>(null);
  // FIX131 — "from fixture.pdf": the file the plan reads, when it names one.
  const sourceName = useConversationFilename(convId, plan.source_file_id);

  async function execute() {
    if (status === "running") return;
    setStatus("running");
    setFailure(null);
    try {
      const format = (plan.format || "docx") as "docx" | "pdf";
      const res = await api.executeDocumentPlan(convId, {
        source_file_id: plan.source_file_id || undefined,
        output_filename: plan.output_filename,
        format,
        plan,
        message_id: serverMessageId(messageId),
      });
      setResult(res);
      setStatus("done");
    } catch (err) {
      // FIX131 — a reason worded in the user's language, never the
      // server's English.
      setFailure(planFailureFromError(err));
      setStatus("error");
    }
  }

  if (status === "done" && result) {
    return <DocResultCard result={result} />;
  }
  // FIX126 F4 — this plan already produced its file (read back after a
  // reload): show the result, not the generate button again.
  const linked =
    status === "idle"
      ? linkedGeneratedFile(files, [plan.format === "pdf" ? "pdf" : "docx"], plan.output_filename)
      : undefined;
  if (linked) {
    return <DocResultCard result={{ file: linked, changes: linked.change_log ?? [] }} />;
  }
  // FIX131 — a failure kept on the message: the card says so after a
  // reload (with a retry) instead of looking untouched.
  const shownFailure =
    status === "error"
      ? failure
      : status === "idle"
        ? persistedPlanFailure(outcomes, "doc-plan", plan.output_filename)
        : undefined;

  const format = (plan.format || "docx").toUpperCase();
  const settings = plan.document_settings || {};
  const language = ((settings.language as string) || "en").toUpperCase();
  const orientation = (settings.orientation as string) || "portrait";
  const pageSize = (settings.page_size as string) || "A4";
  const actions = plan.actions ?? [];

  return (
    <CardShell
      headerIcon={<FileTypeBadge extension={format === "PDF" ? ".pdf" : ".docx"} size="sm" />}
      headerTitle={format === "PDF" ? t("plan.doc.titlePdf") : t("plan.doc.titleDocx")}
      metadata={`${pageSize} ${orientation} · ${language} · ${actions.length} ${t(actions.length === 1 ? "unit.action" : "unit.actions")}`}
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
              : t("plan.doc.generate")}
        </PrimaryButton>
      }
    >
      {shownFailure ? (
        <PlanFailureNotice failure={shownFailure} kind={format === "PDF" ? "PDF" : "Word"} />
      ) : null}
      <ProvenanceLine source={sourceName ?? shownFailure?.sourceFilename} />
      {settings.title_page?.title ? (
        <p className="text-sm italic text-text-secondary">
          &ldquo;{settings.title_page.title}&rdquo;
        </p>
      ) : null}
      {actions.length > 0 ? (
        <PlanList title={t("plan.structure")}>
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

export function DocResultCard({ result }: { result: DocResult }) {
  const t = useT();
  const { file, validation, changes } = result;
  const fileTypeLabel = (file.content_type || "").includes("pdf") ? "PDF" : "DOCX";
  const headerIcon = (
    <FileTypeBadge extension={fileTypeLabel === "PDF" ? ".pdf" : ".docx"} size="sm" />
  );
  const pages = file.version != null
    ? validation?.stats?.page_count
    : validation?.stats?.page_count;
  const words = validation?.stats?.word_count;
  // FIX131 — a document made FROM an input is new, not "v2" of the PDF.
  const madeFromInput = file.made_from?.role === "input";

  return (
    <CardShell
      headerIcon={headerIcon}
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
        <span>{fileTypeLabel}</span>
        <span>{humanSize(file.file_size_bytes)}</span>
        {pages != null ? (
          <span>{pages} {t(pages === 1 ? "unit.page" : "unit.pages")}</span>
        ) : null}
        {words != null ? (
          <span>{words} {t(words === 1 ? "unit.word" : "unit.words")}</span>
        ) : null}
      </MetadataRow>
      {changes && changes.length > 0 ? (
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
      {validation ? (
        <ValidationBadge
          valid={validation.valid ?? false}
          warnings={validation.warnings ?? []}
          errors={validation.errors ?? []}
        />
      ) : null}
    </CardShell>
  );
}

function summarizeDocAction(a: DocAction): string {
  switch (a.type) {
    case "add_title_page":
      return tOutside("plan.doc.titlePage");
    case "add_heading": {
      const lvl = typeof a.level === "number" ? a.level : 1;
      return tOutside("plan.doc.heading", {
        lvl: String(lvl),
        text: truncate((a.text as string) || "", 60),
      });
    }
    case "add_paragraph":
      return tOutside("plan.doc.paragraph", {
        text: truncate((a.text as string) || "", 60),
      });
    case "add_bullet_list": {
      const n = Array.isArray(a.items) ? a.items.length : 0;
      return n === 1
        ? tOutside("plan.doc.bulletList.one")
        : tOutside("plan.doc.bulletList.other", { n: String(n) });
    }
    case "add_numbered_list": {
      const n = Array.isArray(a.items) ? a.items.length : 0;
      return n === 1
        ? tOutside("plan.doc.numberedList.one")
        : tOutside("plan.doc.numberedList.other", { n: String(n) });
    }
    case "add_table": {
      const cols = Array.isArray(a.headers) ? a.headers.length : 0;
      const rows = Array.isArray(a.rows) ? a.rows.length : 0;
      return tOutside("plan.doc.table", {
        cols: String(cols),
        rows: String(rows),
      });
    }
    case "add_page_break":
      return tOutside("plan.doc.pageBreak");
    case "add_horizontal_rule":
      return tOutside("plan.doc.horizontalRule");
    case "add_spacer":
      return tOutside("plan.doc.spacer");
    case "add_blockquote":
      return tOutside("plan.doc.blockquote", {
        text: truncate((a.text as string) || "", 50),
      });
    case "add_code_block":
      return tOutside("plan.doc.codeBlock");
    case "replace_text": {
      const find = (a.find as string) || "";
      const repl = (a.replace as string) || "";
      return tOutside("plan.doc.replaceText", {
        find: truncate(find, 24),
        replace: truncate(repl, 24),
      });
    }
    case "set_header":
      return tOutside("plan.doc.setHeader", {
        text: truncate((a.text as string) || "", 50),
      });
    case "set_footer":
      return tOutside("plan.doc.setFooter", {
        text: truncate((a.text as string) || "", 50),
      });
    default:
      return a.type || tOutside("plan.doc.action");
  }
}
