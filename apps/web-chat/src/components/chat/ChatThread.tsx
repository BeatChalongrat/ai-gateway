"use client";

import { useEffect, useRef, useState } from "react";

import type { Message } from "@/lib/types";

import { EmptyState } from "./EmptyState";
import { MessageBubble } from "./MessageBubble";

interface Props {
  convId: string;
  modelId?: string | null;
  messages: Message[];
  onRegenerate: (messageId: string) => void;
  onPromptPick?: (text: string) => void;
}

const NEAR_BOTTOM_PX = 100;
const SHOW_SCROLL_BUTTON_PX = 200;

export function ChatThread({
  convId,
  modelId,
  messages,
  onRegenerate,
  onPromptPick,
}: Props) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [autoStick, setAutoStick] = useState(true);
  const [farFromBottom, setFarFromBottom] = useState(false);
  // Tracks whether new content arrived while user scrolled up.
  const [pendingNew, setPendingNew] = useState(false);
  const lastLen = useRef(0);
  const lastLastContent = useRef("");
  // Distinguish auto-scroll from user scroll: any scroll the user
  // does NOT initiate should be ignored when deciding to break
  // auto-stick.
  const autoScrolling = useRef(false);

  // Auto-scroll to bottom when:
  //   - a new message arrives (length changed)
  //   - the last message's content grew (streaming)
  // …unless the user has scrolled up (autoStick=false), in which
  // case we mark `pendingNew` so a "↓ New messages" pill appears.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const last = messages[messages.length - 1];
    const lengthGrew = messages.length !== lastLen.current;
    const contentGrew = last?.content !== lastLastContent.current;
    if (!lengthGrew && !contentGrew) return;
    lastLen.current = messages.length;
    lastLastContent.current = last?.content ?? "";

    if (autoStick) {
      autoScrolling.current = true;
      el.scrollTop = el.scrollHeight;
      // Release the autoScrolling flag on next frame so the scroll
      // event handler doesn't mistake the programmatic scroll for a
      // user gesture.
      requestAnimationFrame(() => {
        autoScrolling.current = false;
      });
    } else {
      setPendingNew(true);
    }
  }, [messages, autoStick]);

  // After streaming completes: if user is still "near bottom" (within
  // 100px) snap to bottom to show the full response. The streaming
  // flag transitions to false on the last message; we re-evaluate.
  const anyStreaming = messages.some((m) => m.isStreaming);
  useEffect(() => {
    if (anyStreaming) return;
    const el = scrollerRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distance < NEAR_BOTTOM_PX) {
      autoScrolling.current = true;
      el.scrollTop = el.scrollHeight;
      requestAnimationFrame(() => {
        autoScrolling.current = false;
      });
    }
  }, [anyStreaming]);

  function onScroll(e: React.UIEvent<HTMLDivElement>) {
    if (autoScrolling.current) return;
    const el = e.currentTarget;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distance < NEAR_BOTTOM_PX;
    setAutoStick(nearBottom);
    setFarFromBottom(distance > SHOW_SCROLL_BUTTON_PX);
    if (nearBottom) setPendingNew(false);
  }

  function jumpToBottom() {
    const el = scrollerRef.current;
    if (!el) return;
    setAutoStick(true);
    setPendingNew(false);
    autoScrolling.current = true;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    window.setTimeout(() => {
      autoScrolling.current = false;
    }, 400);
  }

  const lastAsstIdx = lastAssistantIndex(messages);

  return (
    <div className="relative flex-1 overflow-hidden">
      <div
        ref={scrollerRef}
        onScroll={onScroll}
        className="scrollbar-thin h-full overflow-y-auto px-4 py-6 md:px-10"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {messages.length === 0 ? (
            <EmptyState onPrompt={(t) => onPromptPick?.(t)} />
          ) : (
            messages.map((m, i) => {
              const prev = messages[i - 1];
              // 24px between role groups, 16px within the same group.
              const extraGap =
                prev && prev.role !== m.role ? "mt-2" : "";
              return (
                <div key={m.id} className={extraGap}>
                  <MessageBubble
                    convId={convId}
                    modelId={modelId}
                    message={m}
                    showFeedback={
                      m.role === "assistant" &&
                      !m.isStreaming &&
                      m.status === "completed"
                    }
                    showRegenerate={
                      m.role === "assistant" &&
                      i === lastAsstIdx &&
                      !anyStreaming &&
                      m.status !== "regenerated"
                    }
                    onRegenerate={() => onRegenerate(m.id)}
                  />
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* "↓ New messages" pill when content arrives while scrolled up */}
      {pendingNew && !autoStick ? (
        <button
          type="button"
          onClick={jumpToBottom}
          className="focus-ring popover-appear absolute bottom-6 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white shadow-xl hover:bg-accent-hover"
        >
          <svg width={13} height={13} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M12 5v14M5 12l7 7 7-7"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          New messages
        </button>
      ) : farFromBottom ? (
        <button
          type="button"
          aria-label="Scroll to latest"
          onClick={jumpToBottom}
          className="focus-ring popover-appear absolute bottom-6 left-1/2 -translate-x-1/2 rounded-full border border-border bg-bg-surface/85 p-2 text-text-secondary shadow-lg backdrop-blur-sm hover:text-text-primary"
        >
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M6 9l6 6 6-6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      ) : null}
    </div>
  );
}

function lastAssistantIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") return i;
  }
  return -1;
}
