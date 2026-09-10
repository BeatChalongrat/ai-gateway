"use client";

// Inline comparison panel for the version timeline. Mounted under
// the timeline when the user clicks "Compare with vN" on a version
// card.

import { X } from "lucide-react";

import { useT } from "@/lib/i18n";
import type { ComparisonResponse } from "@/lib/types";

import { changeIcon, humanSize } from "./workspaceUtil";

interface Props {
  result: ComparisonResponse;
  onClose: () => void;
}

export function ComparisonCard({ result, onClose }: Props) {
  const t = useT();
  const { v1, v2, comparison } = result;
  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border bg-bg-surface shadow-sm">
      <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-3">
        <span className="text-sm font-semibold text-text-primary">
          {t("ws.comparing", { a: String(v1.version), b: String(v2.version) })}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="focus-ring ml-auto rounded p-1 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary"
          aria-label={t("ws.closeComparison")}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="space-y-3 px-4 py-3">
        <div className="text-xs text-text-muted">
          {t("ws.sizeChange", {
            from: humanSize(v1.file_size_bytes),
            to: humanSize(v2.file_size_bytes),
          })}
          <span className="ml-2 text-text-secondary">({comparison.size_change})</span>
        </div>
        {comparison.structural_changes.length > 0 ? (
          <div>
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-text-muted">
              {t("plan.changes")}
            </div>
            <ul className="space-y-1 text-sm text-text-primary">
              {comparison.structural_changes.map((c, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="mt-0.5 flex-shrink-0">{changeIcon(c.type)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="text-text-secondary">
                      {c.type.replace(/_/g, " ")}
                      {c.sheet ? ` (${c.sheet})` : ""}:
                    </span>{" "}
                    <span>{c.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="rounded-md border border-border-subtle bg-bg-base/40 px-3 py-2 text-xs text-text-secondary">
          {comparison.content_diff_summary}
        </div>
      </div>
    </div>
  );
}
