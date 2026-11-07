"use client";

// Generic tool-activity rows — the client half of the tool_activity SSE
// event (tools without a dedicated indicator: memory/state/past-search/
// canvas, plus any unknown/MCP tool).
//
// Two visual states per row, in first-seen order:
//   running  → breathing cat + shimmer "Using <tool>…"
//   finished → static "✓ <done label>" (no shimmer)
// Finished rows STAY until message_complete clears the list — fast
// tools (save_memory is a single ~ms DB insert) have started/finished
// frames so close together that the running state may never paint, so
// the ✓ row is the only evidence the user gets that the tool ran.
// Visual-only like the other waiting indicators — the ChatThread live
// region announces running tools; done rows are visual confirmation.

import { useT } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { DictKey } from "@/lib/i18n/dict";
import type { ToolActivity } from "@/lib/types";

import { CatIndicator } from "./CatIndicator";

/** Dict key for a known tool's running label, or null → generic
 *  "Using {tool}…" fallback (the future-MCP shape). */
export function toolActivityLabelKey(tool: string): DictKey | null {
  const key = `tool.${tool}`;
  return key in dictionaries.en ? (key as DictKey) : null;
}

/** Same resolution for the done-form label. */
export function toolActivityDoneKey(tool: string): DictKey | null {
  const key = `tool.done.${tool}`;
  return key in dictionaries.en ? (key as DictKey) : null;
}

export function ToolActivityIndicator({
  activities,
}: {
  activities: ToolActivity[];
}) {
  const t = useT();
  if (activities.length === 0) return null;
  return (
    <div className="flex flex-col gap-1 py-1" data-testid="tool-activity">
      {activities.map((a) => {
        if (a.phase === "started") {
          const key = toolActivityLabelKey(a.tool);
          const label = key ? t(key) : t("tool.generic", { tool: a.tool });
          return (
            <CatIndicator key={a.id} labelKey="gen.working" label={label} />
          );
        }
        const doneKey = toolActivityDoneKey(a.tool);
        const doneLabel = doneKey
          ? t(doneKey)
          : t("tool.done.generic", { tool: a.tool });
        return (
          <div
            key={a.id}
            className="flex items-center gap-2 text-sm text-text-muted"
          >
            <span aria-hidden>✓</span>
            <span>{doneLabel}</span>
          </div>
        );
      })}
    </div>
  );
}
