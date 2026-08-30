"use client";

// Phase 4 (desktop shell) — the rich agent tool card.
//
// The web path emits only {id, tool, phase}; the desktop host emits rich
// payloads (arg summary on "started"; success / result / code / screenshot /
// live output on "finished"). This component renders a full card when the
// payload is rich and degrades to the thin running/done row when it is not,
// so both surfaces share one renderer. Nested sub-agent calls (parentId) are
// grouped and rendered indented under their parent card (depth 1).
//
// Match the information the WPF app shows (MessageBubble.xaml tool cards),
// not its pixels.

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

import type { ToolActivity } from "@/lib/types";

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

/** "run_command" → "Run command". A light humaniser; the WPF app localises
 *  via tool.name.* — we keep it self-contained and readable. */
function friendlyName(tool: string): string {
  return tool
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

/** FIX29 §2.2 — one collapsible card per sub-agent (activity.tool ===
 *  "subagent", emitted by the host with the sub-agent id). Collapsed by
 *  default with a one-line summary; opens itself while running, when it
 *  failed, or when a step inside needs the user (consent / failure) — a
 *  collapsed card must never hide something the user has to act on. */
function SubagentCard({ activity, steps, all }: { activity: ToolActivity; steps: ToolActivity[]; all: ToolActivity[] }) {
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
        aria-label={`Sub-agent: ${activity.argsSummary ?? ""}${failed || stepFailed ? " — failed" : ""}`}
      >
        {running ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-text-tertiary" />
        ) : failed || stepFailed ? (
          <X className="h-3.5 w-3.5 shrink-0 text-status-danger" />
        ) : (
          <Check className="h-3.5 w-3.5 shrink-0 text-status-success" />
        )}
        <Cpu className="h-3.5 w-3.5 shrink-0 text-text-tertiary" aria-hidden />
        <span className="shrink-0 text-xs font-medium text-text-secondary">Sub-agent</span>
        <span className="min-w-0 flex-1 truncate text-sm text-text-tertiary" title={activity.argsSummary}>
          {activity.argsSummary}
        </span>
        {!running && (failed || stepFailed) ? (
          <span className="shrink-0 text-xs font-medium text-status-danger">Failed</span>
        ) : null}
        <span className="shrink-0 text-xs text-text-muted">
          {running ? `${done}/${steps.length} steps` : `${steps.length} step${steps.length === 1 ? "" : "s"}`}
          {activity.durationMs != null ? ` · ${Math.round(activity.durationMs / 100) / 10}s` : ""}
        </span>
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-text-muted" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-text-muted" />}
      </button>
      {open ? (
        <div className="border-t border-border-subtle px-2 py-1">
          {steps.map((s) => (
            <ToolCard key={s.id} activity={s} children={all.filter((a) => a.parentId === s.id)} all={all} />
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
}: {
  activity: ToolActivity;
  children?: ToolActivity[];
  all?: ToolActivity[];
}) {
  const running = activity.phase === "started";
  const failed = activity.phase === "finished" && activity.success === false;
  const [expanded, setExpanded] = useState(false);
  if (activity.tool === "subagent") {
    return <SubagentCard activity={activity} steps={children ?? []} all={all ?? []} />;
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
  const rich =
    activity.argsSummary != null ||
    activity.resultPreview != null ||
    activity.input != null ||
    activity.output != null ||
    activity.imageDataUri != null ||
    activity.isCodeRun;

  // Thin fallback (web path): the original one-line running/done row.
  if (!rich) {
    return (
      <div className="flex items-center gap-2 py-1 text-sm" data-testid="tool-activity">
        {running ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-text-tertiary" />
        ) : (
          <Check className="h-3.5 w-3.5 text-status-success" />
        )}
        <span className={running ? "shimmer-text" : "text-text-muted"}>
          {running ? `Using ${friendlyName(activity.tool)}…` : friendlyName(activity.tool)}
        </span>
      </div>
    );
  }

  const hasBody =
    activity.input ||
    activity.resultPreview ||
    activity.output ||
    activity.codeText ||
    activity.imageDataUri;

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
        <span className="shrink-0 text-sm font-medium text-text-primary">
          {friendlyName(activity.tool)}
        </span>
        {activity.argsSummary ? (
          <span className="truncate text-sm text-text-tertiary" title={activity.argsSummary}>
            {activity.argsSummary}
          </span>
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

      {/* screenshot thumbnail — shown INLINE once the card finishes, without
          needing to expand. A screenshot tool whose card shows no image is the
          clearest "I can't tell what it did"; this makes what the model saw
          visible at a useful size right on the card. */}
      {activity.imageDataUri && !running ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={activity.imageDataUri}
          alt="tool screenshot"
          className="mx-3 mb-2 max-h-64 rounded border border-border-subtle"
        />
      ) : null}

      {expanded && !running ? (
        <div className="border-t border-border-subtle px-3 py-2">
          {activity.isCodeRun && activity.codeText ? (
            <>
              <div className="mb-1 text-xs font-medium text-text-tertiary">Code</div>
              <pre className="mb-2 max-h-48 overflow-auto rounded bg-bg-base px-3 py-2 font-mono text-xs text-text-secondary">
                {activity.codeText}
              </pre>
            </>
          ) : activity.input ? (
            <>
              <div className="mb-1 text-xs font-medium text-text-tertiary">Input</div>
              <pre className="mb-2 max-h-32 overflow-auto rounded bg-bg-base px-3 py-2 font-mono text-xs text-text-secondary">
                {activity.input}
              </pre>
            </>
          ) : null}
          {activity.resultPreview ? (
            <>
              <div className="mb-1 text-xs font-medium text-text-tertiary">Output</div>
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
            <ToolCard key={c.id} activity={c} children={(all ?? []).filter((a) => a.parentId === c.id)} all={all} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Renders a flat tool_activities list, grouping nested (parentId) children
 *  under their parent card. Replaces ToolActivityIndicator in hosted mode;
 *  the thin web payloads still render as the one-line rows via ToolCard's
 *  fallback. */
export function ToolActivityList({ activities }: { activities: ToolActivity[] }) {
  const byId = new Map(activities.map((a) => [a.id, a]));
  const roots = activities.filter((a) => !a.parentId || !byId.has(a.parentId));
  const childrenOf = (id: string) => activities.filter((a) => a.parentId === id);
  return (
    <div className="flex flex-col" data-testid="tool-activity-list">
      {roots.map((a) => (
        <ToolCard key={a.id} activity={a} children={childrenOf(a.id)} all={activities} />
      ))}
    </div>
  );
}
