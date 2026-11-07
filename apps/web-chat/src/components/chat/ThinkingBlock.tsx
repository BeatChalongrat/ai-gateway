"use client";

// T4 — Thinking Mode display.
//
// Collapsible block that surfaces the model's reasoning summary for
// an assistant turn. Two rendering states:
//
//   1. Streaming  (is_thinking=true)  — expanded when it mounts (or
//      when thinking starts again), shimmering "Thinking…" header,
//      content scrolls into view as fragments arrive. The person can
//      fold it while it streams (FIX130 B2 — a long qwen thought used
//      to be force-expanded until it ended).
//   2. Complete   (is_thinking=false) — collapses 1s after the
//      thinking ends; header shows "Analyzed Xs". A row that MOUNTS
//      complete (history, a re-mount) starts collapsed — it used to
//      open and fold 1 s later, shifting the page (FIX130 B2). Click
//      to re-expand and re-collapse manually.
//
// FIX130 B2 — whitespace is not a thought. Reasoning that is only
// whitespace (an empty think block, or one the gateway's DLP / echo
// guard emptied) renders nothing once the model stops thinking.
//
// Renders BEFORE the assistant content body so the user reads
// "thought first, then answered" — same UX pattern OpenAI's o1 UI
// uses. The bordered, muted-text content area visually marks it as
// "behind the scenes" rather than first-class output.
//
// No DLP work here — the Gateway's outbound DLP scan on the
// reasoning channel (backend T3) means any text the SSE pump
// delivered to the client is already mask-safe.

import { useEffect, useRef, useState } from "react";

import { BrainCircuit, ChevronDown, ChevronRight } from "lucide-react";

import { useT } from "@/lib/i18n";
import { THINKING_DURATION_FLOOR_MS } from "@/lib/types";

interface ThinkingBlockProps {
  /** Accumulated thinking text. Streams in via the SSE
   *  ``reasoning_delta`` event handler in chatStore. */
  content: string;
  /** True while the model is still reasoning on a LIVE row — the
   *  header reads "Thinking…"; false → "Analyzed". FIX116: renamed from
   *  ``isStreaming``, which it never was (it was ``is_thinking``, and a
   *  finished row could still carry that flag). */
  thinking: boolean;
  /** Total analysis duration in ms — captured between the
   *  ``analysis_status: started`` and ``completed`` events.
   *  Undefined while still streaming. */
  durationMs?: number;
}

export function ThinkingBlock({
  content,
  thinking: isStreaming,
  durationMs,
}: ThinkingBlockProps) {
  const t = useT();
  const hasText = content.trim().length > 0;
  // Expanded while the model thinks so the user sees it; auto-collapse
  // 1s after the thinking ends so the final answer reads as the
  // headline. The 1s delay matches the OpenAI o1 UX — gives the user a
  // beat to start reading the thinking summary before it folds away.
  // A block that mounts on a finished row starts collapsed.
  const [expanded, setExpanded] = useState(isStreaming);
  const contentRef = useRef<HTMLDivElement>(null);

  // Thinking (re)starts — e.g. the next tool round of the same turn —
  // open the block again.
  useEffect(() => {
    if (isStreaming) setExpanded(true);
  }, [isStreaming]);

  useEffect(() => {
    if (!isStreaming && hasText) {
      const timer = window.setTimeout(() => setExpanded(false), 1000);
      return () => window.clearTimeout(timer);
    }
  }, [isStreaming, hasText]);

  // Auto-scroll the inner container while streaming so the latest
  // fragment is always visible. Only fires while expanded — a
  // collapsed block has nothing to scroll.
  useEffect(() => {
    if (isStreaming && expanded && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [content, isStreaming, expanded]);

  if (!hasText && !isStreaming) return null;

  // FIX130 B2 — a near-zero duration timed a replay, not a thought
  // ("Analyzed 0.0s"); print no time rather than a wrong one.
  const durationText =
    !isStreaming &&
    durationMs !== undefined &&
    durationMs >= THINKING_DURATION_FLOOR_MS
      ? t("reasoning.duration", { seconds: (durationMs / 1000).toFixed(1) })
      : null;

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-label={isStreaming ? t("reasoning.thinking") : undefined}
        aria-expanded={expanded && hasText}
        className="flex items-center gap-1.5 text-xs text-text-muted transition-colors hover:text-text-secondary"
      >
        {isStreaming ? (
          // ChatGPT-style "Thinking…" with a bright wave sweeping across
          // the text (pure-CSS background-clip shimmer; static under
          // prefers-reduced-motion). Replaces the old bouncing dots.
          // The chevron appears once there is text to fold.
          <span className="flex items-center gap-1.5">
            <span className="shimmer-text text-xs font-medium">
              {t("reasoning.thinking")}
            </span>
            {hasText ? (
              expanded ? (
                <ChevronDown className="h-3 w-3" aria-hidden />
              ) : (
                <ChevronRight className="h-3 w-3" aria-hidden />
              )
            ) : null}
          </span>
        ) : (
          <span className="flex items-center gap-1.5">
            <BrainCircuit className="h-3.5 w-3.5" aria-hidden />
            <span>{t("reasoning.analyzed")}</span>
            {durationText ? (
              <span className="text-text-muted">{durationText}</span>
            ) : null}
            {expanded ? (
              <ChevronDown className="h-3 w-3" aria-hidden />
            ) : (
              <ChevronRight className="h-3 w-3" aria-hidden />
            )}
          </span>
        )}
      </button>
      {expanded && hasText ? (
        <div
          ref={contentRef}
          className="mt-2 max-h-[300px] overflow-y-auto whitespace-pre-wrap border-l-2 border-border pl-3 text-xs leading-relaxed text-text-muted"
        >
          {content}
        </div>
      ) : null}
    </div>
  );
}
