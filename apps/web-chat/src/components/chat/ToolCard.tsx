"use client";

// Phase 4 (desktop shell) — the rich agent tool card.
//
// The web path emits thin "started" frames ({id, tool, phase, subject?})
// and a richer "finished" frame; the desktop host emits rich payloads on
// both (arg summary + subject on "started"; success / result / code /
// screenshot / live output on "finished"). This component renders a full
// card when the payload is rich and degrades to the thin running/done row
// when it is not, so both surfaces share one renderer. Nested sub-agent
// calls (parentId) are grouped and rendered indented under their parent
// card (depth 1).
//
// FIX77 — what the row SAYS changed: a friendly, localised label
// (tool.<name> / tool.done.<name> in the dictionary, falling back to the
// tool's name alone — never an argument dump) plus at most ONE useful
// argument (`subject`: a filename, a host, a query). The raw argsSummary
// moved into the expanded detail, so what the card used to show stays
// reachable behind the expand. Two rows never render for a call that a
// dedicated card already presents (ask_user_question → QuestionCard,
// update_plan → PlanChecklistCard) — unless that call FAILED, because a
// hidden tool must never fail silently. And a repeated tool's earlier
// image folds behind a "superseded" toggle once a later run of the same
// tool succeeded (collapse, never delete); if the last run failed,
// nothing folds.

import { useEffect, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  Cpu,
  Folder,
  Globe,
  Image as ImageIcon,
  Loader2,
  MonitorSmartphone,
  MousePointerClick,
  Search,
  SquareTerminal,
  Wrench,
  X,
} from "lucide-react";

import { useT } from "@/lib/i18n";
import type { ToolActivity } from "@/lib/types";

import { toolActivityDoneKey, toolActivityLabelKey } from "./ToolActivityIndicator";

const COLLAPSE_DELAY_MS = 1200;

function toolIcon(tool: string) {
  switch (tool) {
    case "run_command":
      return SquareTerminal;
    case "run_python":
      return Code2;
    case "run_subagent":
    case "subagent":
      return Cpu;
    case "web_search":
    case "web_fetch":
    case "open_url":
      return Globe;
    case "read_file":
    case "write_file":
    case "edit_file":
    case "read_document":
    case "list_directory":
    case "create_directory":
    case "move_file":
    case "search_files":
      return Folder;
    case "web_browse":
    case "browser_navigate":
    case "browser_read_page":
      return Globe;
    case "generate_image":
      return ImageIcon;
    case "read_clipboard":
    case "write_clipboard":
      return MousePointerClick;
    case "computer_click":
    case "computer_type":
    case "computer_screenshot":
      return MonitorSmartphone;
    case "web_search_alt":
      return Search;
    default:
      return Wrench;
  }
}

/** "run_command" → "Run command" — the {tool} var for the generic label
 *  when a tool has no dictionary entry. A NAME, never an argument dump. */
function humaniseName(tool: string): string {
  return tool
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

/** FIX77 ② — the localised row label: dictionary entry when the tool has
 *  one, otherwise the generic form around the tool's name alone. */
function useToolLabel(tool: string, running: boolean): string {
  const t = useT();
  const key = running ? toolActivityLabelKey(tool) : toolActivityDoneKey(tool);
  if (key) return t(key);
  return t(running ? "tool.generic" : "tool.done.generic", { tool: humaniseName(tool) });
}

/** FIX77 ③ — rows that a dedicated card in the same message already
 *  presents. Hidden ONLY while they did not fail: a failed call always
 *  keeps its row (a tool must never fail invisibly). */
function presentedByDedicatedCard(a: ToolActivity, planShown: boolean): boolean {
  if (a.phase === "finished" && a.success === false) return false;
  if (a.tool === "ask_user_question") return true; // QuestionCard is the presentation
  if (a.tool === "update_plan" && planShown) return true; // PlanChecklistCard
  return false;
}

/** FIX79 — the images ONE call produced: the multi-image field when the
 *  run saved several alternatives, else the single image. Several images
 *  from one run are alternatives the user must see side by side (the
 *  owner's rule) — supersede only ever folds ACROSS runs, never within. */
function imagesOf(a: ToolActivity): string[] {
  if (a.imageDataUris?.length) return a.imageDataUris;
  return a.imageDataUri ? [a.imageDataUri] : [];
}

/** FIX77 ① — ids whose inline image(s) are superseded: within one sibling
 *  group (same parentId + same tool), every finished image-bearing RUN
 *  before the group's LAST member folds behind a toggle — but only when
 *  that last member did not fail. A failing final attempt collapses
 *  nothing, and a run's several alternatives fold (or stay) together. */
export function supersededImageIds(activities: ToolActivity[]): Set<string> {
  const groups = new Map<string, ToolActivity[]>();
  for (const a of activities) {
    if (a.tool === "subagent") continue;
    const k = `${a.parentId ?? ""}|${a.tool}`;
    const g = groups.get(k);
    if (g) g.push(a);
    else groups.set(k, [a]);
  }
  const out = new Set<string>();
  for (const g of groups.values()) {
    const withImages = g.filter((a) => a.phase === "finished" && imagesOf(a).length > 0);
    if (withImages.length < 2) continue;
    const last = g[g.length - 1]!;
    if (last.phase !== "finished" || last.success === false) continue;
    for (const a of withImages) {
      if (a.id !== withImages[withImages.length - 1]!.id) out.add(a.id);
    }
  }
  return out;
}

/** FIX29 §2.2 — one collapsible card per sub-agent (activity.tool ===
 *  "subagent", emitted by the host with the sub-agent id). Collapsed by
 *  default with a one-line summary; opens itself while running, when it
 *  failed, or when a step inside needs the user (consent / failure) — a
 *  collapsed card must never hide something the user has to act on. */
function SubagentCard({ activity, steps, all, superseded }: { activity: ToolActivity; steps: ToolActivity[]; all: ToolActivity[]; superseded: Set<string> }) {
  const t = useT();
  const running = activity.phase === "started";
  const failed = activity.phase === "finished" && activity.success === false;
  const stepFailed = steps.some((s) => s.phase === "finished" && s.success === false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (running || failed || stepFailed) setOpen(true);
  }, [running, failed, stepFailed]);
  const done = steps.filter((s) => s.phase === "finished").length;
  return (
    <div
      className={`my-1 rounded-lg border ${failed || stepFailed ? "border-status-danger/50" : "border-border-subtle"} bg-bg-surface/60`}
      data-testid="subagent-card"
      data-status={running ? "running" : failed || stepFailed ? "failed" : "ok"}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        // FIX30 §3.1: a sub-agent that did not do its job must READ as failed —
        // in the name (screen readers, the a11y tree) and on the row, not only
        // by the colour of an icon.
        aria-label={`${t("subagent.label")}: ${activity.argsSummary ?? ""}${failed || stepFailed ? ` — ${t("subagent.failed")}` : ""}`}
      >
        {running ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-text-tertiary" />
        ) : failed || stepFailed ? (
          <X className="h-3.5 w-3.5 shrink-0 text-status-danger" />
        ) : (
          <Check className="h-3.5 w-3.5 shrink-0 text-status-success" />
        )}
        <Cpu className="h-3.5 w-3.5 shrink-0 text-text-tertiary" aria-hidden />
        <span className="shrink-0 text-xs font-medium text-text-secondary">{t("subagent.label")}</span>
        <span className="min-w-0 flex-1 truncate text-sm text-text-tertiary" title={activity.argsSummary}>
          {activity.argsSummary}
        </span>
        {!running && (failed || stepFailed) ? (
          <span className="shrink-0 text-xs font-medium text-status-danger">{t("subagent.failed")}</span>
        ) : null}
        <span className="shrink-0 text-xs text-text-muted">
          {running
            ? t("subagent.progress", { done: String(done), total: String(steps.length) })
            : steps.length === 1
              ? t("subagent.step")
              : t("subagent.steps", { n: String(steps.length) })}
          {activity.durationMs != null ? ` · ${Math.round(activity.durationMs / 100) / 10}s` : ""}
        </span>
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-text-muted" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-text-muted" />}
      </button>
      {open ? (
        <div className="border-t border-border-subtle px-2 py-1">
          {steps.map((s) => (
            <ToolCard key={s.id} activity={s} children={all.filter((a) => a.parentId === s.id)} all={all} superseded={superseded} />
          ))}
          {!running && activity.resultPreview ? (
            <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-bg-elevated px-2 py-1 text-xs text-text-secondary">{activity.resultPreview}</pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function ToolCard({
  activity,
  children,
  all,
  superseded,
}: {
  activity: ToolActivity;
  children?: ToolActivity[];
  all?: ToolActivity[];
  superseded?: Set<string>;
}) {
  const t = useT();
  const running = activity.phase === "started";
  const failed = activity.phase === "finished" && activity.success === false;
  const label = useToolLabel(activity.tool, running);
  const [expanded, setExpanded] = useState(false);
  // FIX77 ① — an image a later same-tool run replaced starts folded;
  // the toggle brings it back exactly as it rendered before.
  const imageSuperseded = superseded?.has(activity.id) === true;
  const [showOldImage, setShowOldImage] = useState(false);
  if (activity.tool === "subagent") {
    return <SubagentCard activity={activity} steps={children ?? []} all={all ?? []} superseded={superseded ?? new Set()} />;
  }

  // Auto-expand while running if there is live output to watch; fold shortly
  // after finishing (unless failed — keep failures open).
  useEffect(() => {
    if (running && activity.output) setExpanded(true);
  }, [running, activity.output]);
  useEffect(() => {
    if (!running && !failed) {
      const t = setTimeout(() => setExpanded(false), COLLAPSE_DELAY_MS);
      return () => clearTimeout(t);
    }
    if (failed) setExpanded(true);
  }, [running, failed]);

  const Icon = toolIcon(activity.tool);
  const cardImages = imagesOf(activity);
  const rich =
    activity.argsSummary != null ||
    activity.resultPreview != null ||
    activity.input != null ||
    activity.output != null ||
    cardImages.length > 0 ||
    activity.isCodeRun;

  // Thin fallback (web "started" frames): the original one-line row —
  // friendly label + the one-field subject when the backend sent one.
  if (!rich) {
    return (
      <div className="flex items-center gap-2 py-1 text-sm" data-testid="tool-activity">
        {running ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-text-tertiary" />
        ) : (
          <Check className="h-3.5 w-3.5 text-status-success" />
        )}
        <span className={running ? "shimmer-text" : "text-text-muted"}>{label}</span>
        {activity.subject ? (
          <span className="truncate text-text-muted" data-testid="tool-subject">{activity.subject}</span>
        ) : null}
      </div>
    );
  }

  const hasBody =
    activity.input ||
    activity.argsSummary ||
    activity.resultPreview ||
    activity.output ||
    activity.codeText ||
    cardImages.length > 0;

  const showImages = cardImages.length > 0 && !running && (!imageSuperseded || showOldImage);

  return (
    <div
      className={`rounded-lg border ${failed ? "border-status-danger/50" : "border-border"} bg-bg-surface my-1`}
      data-testid="tool-card"
      data-tool={activity.tool}
      data-status={running ? "running" : failed ? "failed" : "ok"}
    >
      <button
        type="button"
        onClick={() => hasBody && setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        {/* status glyph */}
        {running ? (
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-text-tertiary/60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-text-tertiary" />
          </span>
        ) : failed ? (
          <X className="h-4 w-4 shrink-0 text-status-danger" />
        ) : (
          <Check className="h-4 w-4 shrink-0 text-status-success" />
        )}
        <Icon className="h-4 w-4 shrink-0 text-text-tertiary" />
        <span className="shrink-0 text-sm font-medium text-text-primary" data-testid="tool-label">
          {label}
        </span>
        {activity.subject ? (
          <span className="truncate text-sm text-text-tertiary" title={activity.subject} data-testid="tool-subject">
            {activity.subject}
          </span>
        ) : null}
        {failed ? (
          <span className="shrink-0 text-xs font-medium text-status-danger">{t("tool.failedBadge")}</span>
        ) : null}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {activity.durationMs != null ? (
            <span className="text-xs text-text-muted">{activity.durationMs} ms</span>
          ) : null}
          {hasBody ? (
            expanded ? (
              <ChevronDown className="h-4 w-4 text-text-muted" />
            ) : (
              <ChevronRight className="h-4 w-4 text-text-muted" />
            )
          ) : null}
        </span>
      </button>

      {/* running indeterminate bar */}
      {running ? (
        <div className="mx-3 mb-1 h-0.5 overflow-hidden rounded bg-border">
          <div className="h-full w-1/3 animate-[shimmer_1.4s_infinite] bg-text-tertiary/50" />
        </div>
      ) : null}

      {/* live mini-terminal (run_command) — visible while running too */}
      {activity.output && (running || expanded) ? (
        <pre className="mx-3 mb-2 max-h-40 overflow-auto rounded bg-bg-base px-3 py-2 font-mono text-xs text-text-secondary">
          {activity.output}
        </pre>
      ) : null}

      {/* screenshot / chart — INLINE once the card finishes, without needing
          to expand … unless a LATER run of the same tool superseded it, in
          which case it folds behind this toggle (collapse, never delete).
          FIX79: several images from ONE run are alternatives — they all
          render, and they fold or return together as one set. */}
      {cardImages.length > 0 && !running && imageSuperseded ? (
        <button
          type="button"
          onClick={() => setShowOldImage((v) => !v)}
          className="mx-3 mb-2 flex items-center gap-1 rounded border border-dashed border-border px-2 py-1 text-xs text-text-muted hover:text-text-secondary"
          data-testid="superseded-image-toggle"
          aria-expanded={showOldImage}
        >
          {showOldImage ? <ChevronDown className="h-3 w-3" aria-hidden /> : <ChevronRight className="h-3 w-3" aria-hidden />}
          {showOldImage ? t("tool.earlierImage.hide") : t("tool.earlierImage.show")}
        </button>
      ) : null}
      {showImages
        ? cardImages.map((uri, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={i}
              src={uri}
              alt="tool screenshot"
              className="mx-3 mb-2 max-h-64 rounded border border-border-subtle"
            />
          ))
        : null}

      {expanded && !running ? (
        <div className="border-t border-border-subtle px-3 py-2">
          {activity.isCodeRun && activity.codeText ? (
            <>
              <div className="mb-1 text-xs font-medium text-text-tertiary">{t("tool.section.code")}</div>
              <pre className="mb-2 max-h-48 overflow-auto rounded bg-bg-base px-3 py-2 font-mono text-xs text-text-secondary">
                {activity.codeText}
              </pre>
            </>
          ) : activity.input || activity.argsSummary ? (
            // The raw argument summary the row no longer shows — the
            // Claude Code contract: the call and its result expand together.
            <>
              <div className="mb-1 text-xs font-medium text-text-tertiary">{t("tool.section.input")}</div>
              <pre className="mb-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-bg-base px-3 py-2 font-mono text-xs text-text-secondary" data-testid="tool-input-detail">
                {activity.input ?? activity.argsSummary}
              </pre>
            </>
          ) : null}
          {activity.resultPreview ? (
            <>
              <div className="mb-1 text-xs font-medium text-text-tertiary">{t("tool.section.output")}</div>
              <pre
                className={`max-h-52 overflow-auto rounded bg-bg-base px-3 py-2 font-mono text-xs ${failed ? "text-status-danger" : "text-text-secondary"}`}
              >
                {activity.resultPreview}
              </pre>
            </>
          ) : null}
          {/* the screenshot itself is rendered inline above (no expand needed) */}
        </div>
      ) : null}

      {/* nested children — a run_subagent call now carries one "subagent"
          card per sub-agent (FIX29 §2.2), each holding its own steps */}
      {children && children.length ? (
        <div className="ml-4 border-l-2 border-border-subtle pl-2">
          {children.map((c) => (
            <ToolCard key={c.id} activity={c} children={(all ?? []).filter((a) => a.parentId === c.id)} all={all} superseded={superseded} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Renders a flat tool_activities list, grouping nested (parentId) children
 *  under their parent card. Replaces ToolActivityIndicator in hosted mode;
 *  the thin web payloads still render as the one-line rows via ToolCard's
 *  fallback. FIX77: rows a dedicated card already presents are dropped
 *  (unless failed), and repeated-tool images are marked superseded. */
export function ToolActivityList({ activities, planShown = false }: { activities: ToolActivity[]; planShown?: boolean }) {
  const visible = activities.filter((a) => !presentedByDedicatedCard(a, planShown));
  const superseded = supersededImageIds(visible);
  const byId = new Map(visible.map((a) => [a.id, a]));
  const roots = visible.filter((a) => !a.parentId || !byId.has(a.parentId));
  const childrenOf = (id: string) => visible.filter((a) => a.parentId === id);
  if (visible.length === 0) return null;
  return (
    <div className="flex flex-col" data-testid="tool-activity-list">
      {roots.map((a) => (
        <ToolCard key={a.id} activity={a} children={childrenOf(a.id)} all={visible} superseded={superseded} />
      ))}
    </div>
  );
}
