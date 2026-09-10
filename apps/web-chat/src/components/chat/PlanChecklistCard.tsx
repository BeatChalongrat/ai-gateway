"use client";

// Phase 4 (desktop shell) — the agent's live plan checklist (update_plan).
// Mirrors the WPF plan Expander (MessageBubble.xaml:281): ○ pending /
// ◐ in-progress / ✓ done, done steps struck through, current step emphasised.
// Information parity, not pixel parity.

import { CircleDashed, CircleDotDashed, CircleCheck } from "lucide-react";

import type { PlanStep } from "@/lib/types";

export function PlanChecklistCard({ steps }: { steps: PlanStep[] }) {
  if (!steps.length) return null;
  return (
    <div className="my-1 rounded-lg border border-border bg-bg-surface px-3 py-2" data-testid="plan-card">
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted">
        Plan
      </div>
      <ul className="flex flex-col gap-1">
        {steps.map((s, i) => {
          const Icon =
            s.status === "done" ? CircleCheck : s.status === "in_progress" ? CircleDotDashed : CircleDashed;
          const color =
            s.status === "done"
              ? "text-status-success"
              : s.status === "in_progress"
                ? "text-blue-500"
                : "text-text-muted";
          return (
            <li key={i} className="flex items-start gap-2 text-sm">
              <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${color}`} />
              <span
                className={
                  s.status === "done"
                    ? "text-text-muted line-through"
                    : s.status === "in_progress"
                      ? "font-medium text-text-primary"
                      : "text-text-secondary"
                }
              >
                {s.text}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
