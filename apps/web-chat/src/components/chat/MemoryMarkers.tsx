"use client";

// MemoryMarkers — FIX130 A3b. What an answer SAVED to the user's memory,
// shown once under that answer in a small quiet chip:
//
//   · "บันทึกความจำแล้ว" — a save_memory call stored a memory the user
//     explicitly asked for (active at once).
//   · "เสนอความจำใหม่ — รอยืนยัน" — a save the server did not take as an
//     explicit request is a suggestion; it waits on the Memory page.
//
// FIX132 A — there is no marker for USING memory any more. FIX130 A3a put
// "ใช้ความจำ N รายการ" (with a popover listing the memories) under every
// answer whose prompt carried saved memories; the owner decided memory works
// in the background, as elsewhere: ChatGPT says "Memory updated" only when it
// saves, Claude shows nothing per answer. chat-edge still records which
// memories went into a turn (response_context_sources + a log line) and still
// sends `memory_used`; the page ignores it, and old answers' persisted
// saved_memory rows no longer draw anything.
//
// Data comes from lib/memoryMarkers.ts: the live SSE frame when the page
// watched the turn, the persisted context_sources after a reload.

import Link from "next/link";
import { Check, Sparkles } from "lucide-react";

import { useT } from "@/lib/i18n";
import { messageMemorySaved } from "@/lib/memoryMarkers";
import type { Message } from "@/lib/types";

// chat-edge rewrites the label of a memory the user has since deleted to
// this literal on every older context_sources row, so the text is gone
// everywhere. The answer still saved it, so it stays counted; the chip's
// tooltip names it in words instead of showing the token.
const DELETED_LABEL = "[deleted]";

const CHIP =
  "focus-ring inline-flex items-center gap-1 rounded-full border border-border-subtle px-2 py-0.5 text-[11px] text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-secondary";

export function MessageMemoryMarkers({ message }: { message: Message }) {
  const t = useT();
  const itemLabel = (label: string) => (label === DELETED_LABEL ? t("memory.used.deletedItem") : label);
  const saved = messageMemorySaved(message) ?? [];
  const active = saved.filter((s) => s.status === "active");
  const pending = saved.filter((s) => s.status === "pending_confirmation");
  if (saved.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5" data-testid="memory-markers">
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
