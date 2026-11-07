"use client";

// The thread's scrolling pane: follows the bottom while the reader is there,
// lands at the bottom once the thread first loads (AM5; its own file since
// AM10, when a second chat can sit beside the first).

import { useLayoutEffect, useRef } from "react";

import { Spinner } from "@/components/ui/Spinner";

import { useAmT } from "../i18n";
import type { ThreadItem } from "../types";

export function ThreadScroller({
  items,
  status,
  children,
  testId = "am-thread",
}: {
  items: ThreadItem[];
  status: string;
  children: React.ReactNode;
  /** AM10: the pane beside the chat has its own */
  testId?: string;
}) {
  const t = useAmT();
  const ref = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const seeded = useRef(false);
  const sig = items.length ? `${items.length}:${items[items.length - 1]!.id}:${items.reduce((m, i) => Math.max(m, i.rev), 0)}` : "";

  // Follow the bottom while the reader is there; land at the bottom once
  // the thread first loads.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !items.length) return;
    if (!seeded.current || atBottom.current) {
      el.scrollTop = el.scrollHeight;
      seeded.current = true;
    }
  }, [sig, items.length]);

  return (
    <div
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      }}
      className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-10"
      aria-live="polite"
      aria-busy={status === "loading"}
      aria-label={t("ws.threadAria")}
      role="log"
      data-testid={testId}
    >
      <div className="mx-auto flex w-full max-w-chat flex-col gap-4 pb-10">
        {status === "loading" && !items.length ? (
          <div className="flex items-center gap-2 text-sm text-text-muted">
            <Spinner size={14} />
            <span>{t("common.loading")}</span>
          </div>
        ) : null}
        {children}
        {status === "reconnecting" ? (
          <div role="status" className="flex items-center gap-2 self-center text-xs text-text-muted">
            <Spinner size={12} />
            <span>{t("ws.reconnecting")}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
