"use client";

// Phase 4 — Excel Smart Workspace card pair (polished in Session 4
// with Lucide icons via the shared planCardUI shell).
//
// Lifecycle:
//   1. AI message contains a :::excel-plan ... ::: fenced block.
//      MessageBubble parses + renders <ExcelPlanCard plan={...}/>.
//   2. User clicks Execute Plan → POST /excel/execute. The card
//      flips to loading, then to <ExcelResultCard/>.
//   3. ResultCard shows the generated file (download), change log,
//      validation, and version/size badges.

import { useState, type ReactNode } from "react";

import {
  BarChart3,
  Filter,
  Merge,
  Paintbrush,
  PenLine,
  Plus,
  RotateCw,
  Sheet,
  Snowflake,
  Table2,
} from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { api } from "@/lib/api";
import { tOutside, useT } from "@/lib/i18n";
import {
  persistedPlanFailure,
  planFailureFromError,
  type PlanFailure,
} from "@/lib/planFailure";
import type { ExcelExecuteResponse, FileAttachment, PlanOutcome } from "@/lib/types";

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
  useConversationFilename,
} from "./planCardUI";

/** FIX126 F4 — a restored card has no validator report. */
export type ExcelResult = Pick<ExcelExecuteResponse, "file" | "changes"> &
  Partial<Pick<ExcelExecuteResponse, "validation">>;

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

// All action icons render in text-text-muted so the plan list
// reads as structural metadata. The icon SHAPE alone carries the
// "what kind of mutation" hint; colour was removed in Session 5.1
// because the cards were starting to look like an AI template
// brochure and the visual hierarchy was lost (blue CTA needs to
// be the only thing that pops).
const ACTION_ICON_CLASS = "h-4 w-4 text-text-muted";
const ACTION_ICONS: Record<string, ReactNode> = {
  create_sheet:     <Plus className={ACTION_ICON_CLASS} />,
  delete_sheet:     <Plus className={`${ACTION_ICON_CLASS} -rotate-45`} />,
  rename_sheet:     <PenLine className={ACTION_ICON_CLASS} />,
  write_cells:      <PenLine className={ACTION_ICON_CLASS} />,
  write_table:      <Table2 className={ACTION_ICON_CLASS} />,
  create_chart:     <BarChart3 className={ACTION_ICON_CLASS} />,
  format_range:     <Paintbrush className={ACTION_ICON_CLASS} />,
  set_column_width: <Sheet className={ACTION_ICON_CLASS} />,
  merge_cells:      <Merge className={ACTION_ICON_CLASS} />,
  auto_filter:      <Filter className={ACTION_ICON_CLASS} />,
  freeze_panes:     <Snowflake className={ACTION_ICON_CLASS} />,
};

function actionIcon(type?: string): ReactNode {
  if (type && ACTION_ICONS[type]) return ACTION_ICONS[type];
  return <Sheet className={ACTION_ICON_CLASS} />;
}

interface Props {
  convId: string;
  plan: ExcelPlan;
  /** FIX126 F4 — the assistant message that wrote the plan, and its files. */
  messageId?: string;
  files?: FileAttachment[];
  /** FIX131 — the message's failed plan executions (GET /messages). */
  outcomes?: PlanOutcome[];
}

export function ExcelPlanCard({ convId, plan, messageId, files, outcomes }: Props) {
  const t = useT();
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [failure, setFailure] = useState<PlanFailure | null>(null);
  const [result, setResult] = useState<ExcelExecuteResponse | null>(null);
  const sourceName = useConversationFilename(convId, plan.source_file_id);

  async function execute() {
    if (status === "running") return;
    setStatus("running");
    setFailure(null);
    try {
      // No source_file_id → create-from-scratch (Phase 8). The server
      // forces operation=create_workbook in that mode, so a plan the
      // model wrote without an attachment executes instead of 404ing.
      const res = await api.executeExcelPlan(convId, {
        source_file_id: plan.source_file_id || undefined,
        output_filename: plan.output_filename,
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
    return <ExcelResultCard result={result} />;
  }
  // FIX126 F4 — the workbook this plan produced, read back after a reload.
  const linked =
    status === "idle" ? linkedGeneratedFile(files, ["xlsx"], plan.output_filename) : undefined;
  if (linked) {
    return <ExcelResultCard result={{ file: linked, changes: linked.change_log ?? [] }} />;
  }
  // FIX131 — a failure kept on the message survives the reload.
  const shownFailure =
    status === "error"
      ? failure
      : status === "idle"
        ? persistedPlanFailure(outcomes, "excel-plan", plan.output_filename)
        : undefined;

  const actions = plan.actions ?? [];

  return (
    <CardShell
      headerIcon={<FileTypeBadge extension=".xlsx" size="sm" />}
      headerTitle={t("plan.excel.title")}
      metadata={`${actions.length} ${t(actions.length === 1 ? "unit.action" : "unit.actions")}`}
      footer={
        <PrimaryButton
          onClick={execute}
          loading={status === "running"}
          icon={shownFailure ? <RotateCw className="h-4 w-4" /> : undefined}
        >
          {status === "running"
            ? t("plan.processing")
            : shownFailure
              ? t("plan.retry")
              : t("plan.execute")}
        </PrimaryButton>
      }
    >
      {shownFailure ? <PlanFailureNotice failure={shownFailure} kind="Excel" /> : null}
      <ProvenanceLine source={sourceName ?? shownFailure?.sourceFilename} />
      {plan.description ? (
        <p className="text-sm italic text-text-secondary">
          &ldquo;{plan.description}&rdquo;
        </p>
      ) : null}
      {actions.length > 0 ? (
        <PlanList title={t("plan.changes")}>
          {actions.map((a, i) => (
            <PlanListItem key={i} icon={actionIcon(a.type)}>
              {summarizeExcelAction(a)}
            </PlanListItem>
          ))}
        </PlanList>
      ) : null}
    </CardShell>
  );
}

export function ExcelResultCard({ result }: { result: ExcelResult }) {
  const t = useT();
  const { file, validation, changes } = result;
  const sheets = validation?.stats?.sheets;
  const charts = validation?.stats?.charts;
  const brokenCount = validation?.stats?.broken_formulas ?? 0;
  const madeFromInput = file.made_from?.role === "input"; // FIX131

  return (
    <CardShell
      headerIcon={<FileTypeBadge extension=".xlsx" size="sm" />}
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
        <span>Excel</span>
        <span>{humanSize(file.file_size_bytes)}</span>
        {sheets != null ? (
          <span>{sheets} {t(sheets === 1 ? "unit.sheet" : "unit.sheets")}</span>
        ) : null}
        {charts != null ? (
          <span>{charts} {t(charts === 1 ? "unit.chart" : "unit.charts")}</span>
        ) : null}
      </MetadataRow>
      {changes && changes.length > 0 ? (
        <PlanList title={t("plan.changesMade", { n: String(changes.length) })}>
          {changes.map((line, i) => (
            <PlanListItem
              key={i}
              icon={
                <Plus
                  className={`h-4 w-4 ${
                    line.startsWith("ERROR") ? "text-status-danger" : "text-text-muted"
                  }`}
                />
              }
            >
              {line}
            </PlanListItem>
          ))}
        </PlanList>
      ) : null}
      {validation ? (
        <ValidationBadge
          valid={validation.valid ?? false}
          warnings={validation.warnings ?? []}
          errors={validation.issues ?? []}
          okMessage={
            brokenCount === 0
              ? t("plan.validation.okNoFormulas")
              : t("plan.validation.ok")
          }
        />
      ) : null}
    </CardShell>
  );
}

function summarizeExcelAction(a: ExcelPlanAction): string {
  const sheet = typeof a.sheet_name === "string" ? a.sheet_name : "?";
  switch (a.type) {
    case "create_sheet":
      return tOutside("plan.excel.createSheet", { sheet });
    case "delete_sheet":
      return tOutside("plan.excel.deleteSheet", { sheet });
    case "rename_sheet":
      return tOutside("plan.excel.renameSheet", {
        sheet,
        name: (a as { new_name?: string }).new_name ?? "?",
      });
    case "write_cells": {
      const cells = (a as { cells?: unknown[] }).cells;
      const n = Array.isArray(cells) ? cells.length : 0;
      return n === 1
        ? tOutside("plan.excel.writeCells.one", { sheet })
        : tOutside("plan.excel.writeCells.other", { sheet, n: String(n) });
    }
    case "write_table":
      return tOutside("plan.excel.writeTable", { sheet });
    case "create_chart": {
      const title = (a as { title?: string }).title || "chart";
      const kind = (a as { chart_type?: string }).chart_type || "bar";
      return tOutside("plan.excel.createChart", { sheet, title, kind });
    }
    case "format_range":
      return tOutside("plan.excel.formatRange", { sheet });
    case "set_column_width":
      return tOutside("plan.excel.setColumnWidth", { sheet });
    case "merge_cells":
      return tOutside("plan.excel.mergeCells", { sheet });
    case "auto_filter":
      return tOutside("plan.excel.autoFilter", { sheet });
    case "freeze_panes":
      return tOutside("plan.excel.freezePanes", { sheet });
    default:
      return tOutside("plan.excel.genericAction", {
        type: a.type || tOutside("plan.doc.action"),
        sheet,
      });
  }
}
