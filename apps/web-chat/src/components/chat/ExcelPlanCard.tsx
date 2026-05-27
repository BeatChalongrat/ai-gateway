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
  Sheet,
  Snowflake,
  Table2,
} from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type { ExcelExecuteResponse } from "@/lib/types";

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
} from "./planCardUI";

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

// Icon palette — colour-coded by intent so the user can skim a
// plan list and tell mutating ops apart from formatting ops:
// emerald = creating structure, blue = writing data, purple =
// formatting, indigo = layout, slate = neutral, cyan = freeze.
const ACTION_ICONS: Record<string, ReactNode> = {
  create_sheet:     <Plus className="h-4 w-4 text-emerald-500" />,
  delete_sheet:     <Plus className="h-4 w-4 -rotate-45 text-rose-500" />,
  rename_sheet:     <PenLine className="h-4 w-4 text-indigo-500" />,
  write_cells:      <PenLine className="h-4 w-4 text-blue-500" />,
  write_table:      <Table2 className="h-4 w-4 text-slate-500" />,
  create_chart:     <BarChart3 className="h-4 w-4 text-emerald-500" />,
  format_range:     <Paintbrush className="h-4 w-4 text-purple-500" />,
  set_column_width: <Sheet className="h-4 w-4 text-slate-500" />,
  merge_cells:      <Merge className="h-4 w-4 text-indigo-500" />,
  auto_filter:      <Filter className="h-4 w-4 text-slate-500" />,
  freeze_panes:     <Snowflake className="h-4 w-4 text-cyan-500" />,
};

function actionIcon(type?: string): ReactNode {
  if (type && ACTION_ICONS[type]) return ACTION_ICONS[type];
  return <Sheet className="h-4 w-4 text-text-tertiary" />;
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
      if (!srcId) throw new Error("plan is missing source_file_id");
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

  const actions = plan.actions ?? [];

  return (
    <CardShell
      headerIcon={<Sheet className="h-5 w-5 text-emerald-500" />}
      headerTitle="Excel Edit Plan"
      metadata={`${actions.length} action${actions.length === 1 ? "" : "s"}`}
      footer={
        <>
          <PrimaryButton onClick={execute} loading={status === "running"}>
            {status === "running" ? "Processing…" : "Execute Plan"}
          </PrimaryButton>
          {error ? <ErrorLine message={error} /> : null}
        </>
      }
    >
      {plan.description ? (
        <p className="text-sm italic text-text-secondary">
          &ldquo;{plan.description}&rdquo;
        </p>
      ) : null}
      {actions.length > 0 ? (
        <PlanList title="Changes">
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

export function ExcelResultCard({ result }: { result: ExcelExecuteResponse }) {
  const { file, validation, changes } = result;
  const sheets = validation?.stats?.sheets;
  const charts = validation?.stats?.charts;
  const brokenCount = validation?.stats?.broken_formulas ?? 0;

  return (
    <CardShell
      headerIcon={<Sheet className="h-5 w-5 text-emerald-500" />}
      headerTitle={file.filename}
      metadata={file.version ? `v${file.version}` : undefined}
      footer={
        <DownloadButton fileId={file.id} filename={file.filename} />
      }
    >
      <MetadataRow>
        <span>Excel</span>
        <span>{humanSize(file.file_size_bytes)}</span>
        {sheets != null ? (
          <span>{sheets} sheet{sheets === 1 ? "" : "s"}</span>
        ) : null}
        {charts != null ? (
          <span>{charts} chart{charts === 1 ? "" : "s"}</span>
        ) : null}
      </MetadataRow>
      {changes && changes.length > 0 ? (
        <PlanList title={`Changes made (${changes.length})`}>
          {changes.map((line, i) => (
            <PlanListItem
              key={i}
              icon={
                <Plus
                  className={`h-4 w-4 ${
                    line.startsWith("ERROR") ? "text-status-danger" : "text-emerald-500"
                  }`}
                />
              }
            >
              {line}
            </PlanListItem>
          ))}
        </PlanList>
      ) : null}
      <ValidationBadge
        valid={validation?.valid ?? false}
        warnings={validation?.warnings ?? []}
        errors={validation?.issues ?? []}
        okMessage={
          brokenCount === 0
            ? "File is valid, no broken formulas."
            : "File is valid."
        }
      />
    </CardShell>
  );
}

function summarizeExcelAction(a: ExcelPlanAction): string {
  const sheet = typeof a.sheet_name === "string" ? a.sheet_name : "?";
  switch (a.type) {
    case "create_sheet":
      return `Create sheet "${sheet}"`;
    case "delete_sheet":
      return `Delete sheet "${sheet}"`;
    case "rename_sheet":
      return `Rename "${sheet}" → "${(a as { new_name?: string }).new_name ?? "?"}"`;
    case "write_cells": {
      const cells = (a as { cells?: unknown[] }).cells;
      const n = Array.isArray(cells) ? cells.length : 0;
      return `Write ${n} cell${n === 1 ? "" : "s"} in "${sheet}"`;
    }
    case "write_table":
      return `Write table in "${sheet}"`;
    case "create_chart": {
      const title = (a as { title?: string }).title || "chart";
      const kind = (a as { chart_type?: string }).chart_type || "bar";
      return `Create ${kind} chart "${title}" in "${sheet}"`;
    }
    case "format_range":
      return `Format range in "${sheet}"`;
    case "set_column_width":
      return `Set column widths in "${sheet}"`;
    case "merge_cells":
      return `Merge cells in "${sheet}"`;
    case "auto_filter":
      return `Add filter in "${sheet}"`;
    case "freeze_panes":
      return `Freeze panes in "${sheet}"`;
    default:
      return `${a.type || "action"} in "${sheet}"`;
  }
}
