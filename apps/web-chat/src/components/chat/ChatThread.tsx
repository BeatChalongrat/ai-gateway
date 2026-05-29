"use client";

// Chat thread.
//
// Two rendering modes — plain map for short threads, virtualized
// list (via @tanstack/react-virtual) once the count crosses
// VIRTUALIZE_THRESHOLD. Both modes share the same scroll container,
// the same auto-scroll behaviour, and the same "↓ New messages" /
// scroll-to-bottom pills.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import type { Message } from "@/lib/types";
import { hasMultipleModels } from "@/stores/chatStore";

import { EmptyState } from "./EmptyState";
import { MessageBubble } from "./MessageBubble";

interface Props {
  convId: string;
  modelId?: string | null;
  messages: Message[];
  onRegenerate: (messageId: string) => void;
  onPromptPick?: (text: string) => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onSwitchBranch?: (messageId: string, branchIndex: number) => void;
  /** Older-page lazy-load. ChatThread renders a "Load earlier" pill
   *  at the top of the scroll container; clicking calls the
   *  parent's loader. After older messages are prepended the
   *  scroll position is preserved automatically. */
  hasMoreOlder?: boolean;
  loadingOlder?: boolean;
  onLoadEarlier?: () => Promise<void> | void;
}

const NEAR_BOTTOM_PX = 100;
const SHOW_SCROLL_BUTTON_PX = 200;
const VIRTUALIZE_THRESHOLD = 50;

export function ChatThread({
  convId,
  modelId,
  messages,
  onRegenerate,
  onPromptPick,
  onEditMessage,
  onSwitchBranch,
  hasMoreOlder,
  loadingOlder,
  onLoadEarlier,
}: Props) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [autoStick, setAutoStick] = useState(true);
  const [farFromBottom, setFarFromBottom] = useState(false);
  const [pendingNew, setPendingNew] = useState(false);
  const lastLen = useRef(0);
  const lastLastContent = useRef("");
  const autoScrolling = useRef(false);

  // Track the top message id + scroll offset BEFORE every render so
  // a prepend (older-page load) can restore the user's viewport.
  const prevFirstId = useRef<string | null>(null);
  const preservedTopHeight = useRef<number | null>(null);

  // useLayoutEffect runs synchronously after DOM mutations but
  // BEFORE the browser paints — that's the only place where a
  // scrollTop adjustment will be invisible to the user.
  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const newFirstId = messages[0]?.id ?? null;
    const isPrepend =
      preservedTopHeight.current != null &&
      newFirstId !== null &&
      newFirstId !== prevFirstId.current;
    if (isPrepend) {
      const delta = el.scrollHeight - preservedTopHeight.current!;
      el.scrollTop = el.scrollTop + delta;
    }
    prevFirstId.current = newFirstId;
    preservedTopHeight.current = null;
  });

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
      requestAnimationFrame(() => {
        autoScrolling.current = false;
      });
    } else {
      setPendingNew(true);
    }
  }, [messages, autoStick]);

  // After streaming completes: if user is still "near bottom" (within
  // 100px) snap to bottom to show the full response.
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

  async function handleLoadEarlier() {
    if (!onLoadEarlier || loadingOlder || !hasMoreOlder) return;
    const el = scrollerRef.current;
    if (el) preservedTopHeight.current = el.scrollHeight;
    try {
      await onLoadEarlier();
    } catch {
      preservedTopHeight.current = null;
    }
  }

  const lastAsstIdx = useMemo(() => lastAssistantIndex(messages), [messages]);
  const showModelBadges = useMemo(() => hasMultipleModels(messages), [messages]);

  const shouldVirtualize = messages.length >= VIRTUALIZE_THRESHOLD;

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
            <>
              <LoadEarlierBar
                visible={!!hasMoreOlder}
                loading={!!loadingOlder}
                onClick={handleLoadEarlier}
              />
              {shouldVirtualize ? (
                <VirtualMessageList
                  scrollerRef={scrollerRef}
                  convId={convId}
                  modelId={modelId}
                  messages={messages}
                  showModelBadges={showModelBadges}
                  lastAsstIdx={lastAsstIdx}
                  anyStreaming={anyStreaming}
                  onRegenerate={onRegenerate}
                  onEditMessage={onEditMessage}
                  onSwitchBranch={onSwitchBranch}
                />
              ) : (
                <PlainMessageList
                  convId={convId}
                  modelId={modelId}
                  messages={messages}
                  showModelBadges={showModelBadges}
                  lastAsstIdx={lastAsstIdx}
                  anyStreaming={anyStreaming}
                  onRegenerate={onRegenerate}
                  onEditMessage={onEditMessage}
                  onSwitchBranch={onSwitchBranch}
                />
              )}
            </>
          )}
        </div>
      </div>

      {pendingNew && !autoStick ? (
        <button
          type="button"
          onClick={jumpToBottom}
          className="focus-ring popover-appear absolute bottom-6 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-text-primary px-3 py-1.5 text-xs font-medium text-bg-base shadow-xl hover:opacity-90"
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

// ─── Inner list renderers ──────────────────────────────────────

interface InnerProps {
  convId: string;
  modelId?: string | null;
  messages: Message[];
  showModelBadges: boolean;
  lastAsstIdx: number;
  anyStreaming: boolean;
  onRegenerate: (messageId: string) => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onSwitchBranch?: (messageId: string, branchIndex: number) => void;
}

function PlainMessageList(props: InnerProps) {
  return (
    <>
      {props.messages.map((m, i) => renderRow(m, i, props))}
    </>
  );
}

function VirtualMessageList(
  props: InnerProps & {
    scrollerRef: React.RefObject<HTMLDivElement>;
  },
) {
  const { scrollerRef, messages } = props;
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollerRef.current,
    estimateSize: () => 200,
    overscan: 5,
    measureElement(element) {
      // Some browsers report 0 during a transition; the virtualizer
      // tolerates it but a measured pixel value is preferable.
      return element?.getBoundingClientRect().height ?? 0;
    },
  });

  const items = virtualizer.getVirtualItems();
  return (
    <div
      style={{
        position: "relative",
        height: virtualizer.getTotalSize(),
        width: "100%",
      }}
    >
      {items.map((vi) => {
        const m = messages[vi.index];
        return (
          <div
            key={m.id}
            data-index={vi.index}
            ref={virtualizer.measureElement}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${vi.start}px)`,
            }}
          >
            {renderRow(m, vi.index, props)}
          </div>
        );
      })}
    </div>
  );
}

function renderRow(m: Message, i: number, props: InnerProps) {
  const {
    convId,
    modelId,
    messages,
    showModelBadges,
    lastAsstIdx,
    anyStreaming,
    onRegenerate,
    onEditMessage,
    onSwitchBranch,
  } = props;
  const prev = messages[i - 1];
  const extraGap = prev && prev.role !== m.role ? "mt-2" : "";
  return (
    <div className={extraGap}>
      <MessageBubble
        convId={convId}
        modelId={modelId}
        message={m}
        showModelBadge={showModelBadges}
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
        onEdit={
          m.role === "user" && onEditMessage && !anyStreaming
            ? (text) => onEditMessage(m.id, text)
            : undefined
        }
        onSwitchBranch={
          onSwitchBranch ? (idx) => onSwitchBranch(m.id, idx) : undefined
        }
      />
    </div>
  );
}

function LoadEarlierBar({
  visible,
  loading,
  onClick,
}: {
  visible: boolean;
  loading: boolean;
  onClick: () => void;
}) {
  if (!visible) return null;
  return (
    <div className="-mt-2 flex justify-center pb-2">
      <button
        type="button"
        onClick={onClick}
        disabled={loading}
        className="focus-ring inline-flex items-center gap-1.5 rounded-full border border-border bg-bg-secondary/70 px-3 py-1 text-[11px] text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-wait disabled:opacity-60"
      >
        {loading ? (
          <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
        ) : (
          <svg width={11} height={11} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M6 15l6-6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
        Load earlier messages
      </button>
    </div>
  );
}

function lastAssistantIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") return i;
  }
  return -1;
}
