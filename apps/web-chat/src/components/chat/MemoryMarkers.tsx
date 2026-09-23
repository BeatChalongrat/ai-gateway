"use client";

// MemoryMarkers — FIX130 A3a/A3b. What an answer did with the user's memory,
// shown under it in small quiet chips:
//
//   · "ใช้ความจำ N รายการ" — saved memories went into this answer's prompt.
//     Clicking lists them (short text) with a link to the Memory page.
//   · "บันทึกความจำแล้ว" — a save_memory call stored a memory the user
//     explicitly asked for (active at once).
//   · "เสนอความจำใหม่ — รอยืนยัน" — a save the server did not take as an
//     explicit request is a suggestion; it waits on the Memory page.
//
// NIGHT2: the only trace of memory on an answer was the Sources rail, which
// MessageBubble hides (SHOW_SOURCES_RAIL = false), so "how did it know my
// name?" had no answer on screen. These chips are independent of that rail.
// Data comes from lib/memoryMarkers.ts: the live SSE frame when the page
// watched the turn, the persisted context_sources after a reload.

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { Brain, Check, Sparkles } from "lucide-react";

import { useDismissable } from "@/hooks/useDismissable";
import { useT } from "@/lib/i18n";
import { messageMemorySaved, messageMemoryUsed, type MemoryUsed } from "@/lib/memoryMarkers";
import type { Message } from "@/lib/types";

// chat-edge rewrites the label of a memory the user has since deleted to
// this literal on every older context_sources row, so the text is gone
// everywhere. The answer still used (or saved) it, so it stays counted; the
// page names it in words instead of showing the token.
const DELETED_LABEL = "[deleted]";

function useItemLabel(): (label: string) => string {
  const t = useT();
  return (label) => (label === DELETED_LABEL ? t("memory.used.deletedItem") : label);
}

const CHIP =
  "focus-ring inline-flex items-center gap-1 rounded-full border border-border-subtle px-2 py-0.5 text-[11px] text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-secondary";

export function MessageMemoryMarkers({ message }: { message: Message }) {
  const itemLabel = useItemLabel();
  const t = useT();
  const used = messageMemoryUsed(message);
  const saved = messageMemorySaved(message) ?? [];
  const active = saved.filter((s) => s.status === "active");
  const pending = saved.filter((s) => s.status === "pending_confirmation");
  if (!used && saved.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5" data-testid="memory-markers">
      {used ? <MemoryUsedChip used={used} /> : null}
      {active.length > 0 ? (
        <Link
          href="/chat/memory"
          className={CHIP}
          data-testid="memory-saved-chip"
          title={active.map((s) => itemLabel(s.label)).join("\n")}
        >
          <Check className="h-3 w-3" aria-hidden />
          <span>{t("memory.saved.active")}</span>
        </Link>
      ) : null}
      {pending.length > 0 ? (
        <Link
          href="/chat/memory"
          className={CHIP}
          data-testid="memory-suggested-chip"
          title={pending.map((s) => itemLabel(s.label)).join("\n")}
        >
          <Sparkles className="h-3 w-3" aria-hidden />
          <span>{t("memory.saved.pending")}</span>
        </Link>
      ) : null}
    </div>
  );
}

function MemoryUsedChip({ used }: { used: MemoryUsed }) {
  const itemLabel = useItemLabel();
  const t = useT();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismissable(wrapRef, open, close);
  const label = used.count === 1 ? t("memory.used.one") : t("memory.used.many", { n: String(used.count) });
  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={CHIP}
        data-testid="memory-used-chip"
      >
        <Brain className="h-3 w-3" aria-hidden />
        <span>{label}</span>
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label={t("memory.used.title")}
          data-testid="memory-used-popover"
          className="popover-appear absolute left-0 top-full z-30 mt-1 w-72 overflow-hidden rounded-xl border border-border bg-bg-surface shadow-2xl"
        >
          <div className="border-b border-border-subtle px-3 py-2 text-xs font-semibold text-text-primary">
            {t("memory.used.title")}
          </div>
          {used.items.length > 0 ? (
            <ul className="max-h-48 overflow-y-auto px-3 py-2 text-xs text-text-secondary">
              {used.items.map((it) => (
                <li key={it.id} className="py-0.5">
                  {itemLabel(it.label)}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="border-t border-border-subtle px-3 py-2">
            <Link
              href="/chat/memory"
              className="text-xs text-text-secondary underline-offset-2 hover:text-text-primary hover:underline"
            >
              {t("memory.used.manage")}
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}
