"use client";

// T4 — Thinking Mode display.
//
// Collapsible block that surfaces the model's reasoning summary for
// an assistant turn. Two rendering states:
//
//   1. Streaming  (is_thinking=true)  — auto-expanded, bouncing-dot
//      "Analyzing" header, content scrolls into view as fragments
//      arrive. Stays expanded until ``is_thinking`` flips false.
//   2. Complete   (is_thinking=false) — collapses 1s after the
//      stream finishes; header shows "Analyzed Xs". Click to
//      re-expand and re-collapse manually.
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

interface ThinkingBlockProps {
  /** Accumulated thinking text. Streams in via the SSE
   *  ``reasoning_delta`` event handler in chatStore. */
  content: string;
  /** True while the chat-edge is still forwarding reasoning
   *  fragments (bracketed by ``analysis_status`` started/completed). */
  isStreaming: boolean;
  /** Total analysis duration in ms — captured between the
   *  ``analysis_status: started`` and ``completed`` events.
   *  Undefined while still streaming. */
  durationMs?: number;
}

export function ThinkingBlock({
  content,
  isStreaming,
  durationMs,
}: ThinkingBlockProps) {
  const t = useT();
  // Auto-expanded while streaming so the user sees the model
  // thinking; auto-collapse 1s after streaming completes so the
  // final answer reads as the headline. The 1s delay matches the
  // OpenAI o1 UX — gives the user a beat to start reading the
  // thinking summary before it folds away.
  const [expanded, setExpanded] = useState(true);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isStreaming && content) {
      const timer = window.setTimeout(() => setExpanded(false), 1000);
      return () => window.clearTimeout(timer);
    }
  }, [isStreaming, content]);

  // Auto-scroll the inner container while streaming so the latest
  // fragment is always visible. Only fires while expanded — a
  // collapsed block has nothing to scroll.
  useEffect(() => {
    if (isStreaming && expanded && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [content, isStreaming, expanded]);

  if (!content && !isStreaming) return null;

  const durationText =
    !isStreaming && durationMs !== undefined
      ? t("reasoning.duration", { seconds: (durationMs / 1000).toFixed(1) })
      : null;

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        // During streaming the button is non-interactive in practice (the
        // block is force-expanded) — it just hosts the shimmer label.
        aria-label={isStreaming ? t("reasoning.thinking") : undefined}
        className="flex items-center gap-1.5 text-xs text-text-muted transition-colors hover:text-text-secondary"
      >
        {isStreaming ? (
          // ChatGPT-style "Thinking…" with a bright wave sweeping across
          // the text (pure-CSS background-clip shimmer; static under
          // prefers-reduced-motion). Replaces the old bouncing dots.
          <span className="shimmer-text text-xs font-medium">
            {t("reasoning.thinking")}
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
      {(expanded || isStreaming) && content ? (
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
