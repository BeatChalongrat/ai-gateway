"use client";

// TemporaryChatBanner — the "this chat is not being remembered" strip,
// shown directly above the composer whenever memory is off or the chat is
// temporary.
//
// Why this exists when MemoryIndicator already shows the same state:
// the indicator lives in the conversation header, and that header is
// `hidden md:flex`. Below the md breakpoint — a narrow window, the
// desktop app resized, a phone — the whole row is not rendered, so the
// only signal that nothing is being remembered disappeared exactly
// when the user could least afford to guess. This strip has no
// breakpoint: if memory is off, it is on screen.
//
// It sits above the composer rather than at the top of the thread
// because the thread scrolls and the composer does not. A banner the
// user scrolls past is a banner that is not "always visible".
//
// Rendering nothing when memory is ON is deliberate: the normal state
// is the one the user already expects, and a permanent "memory is on"
// bar would train people to ignore the strip they need to notice.

import { BrainCircuit } from "lucide-react";

import { toast } from "@/components/ui/Toast";
import { tOutside, useT } from "@/lib/i18n";
import { memoryModeOf } from "@/lib/memoryMode";
import { useChatStore } from "@/stores/chatStore";

// FIX130 A2 — two states, two sentences, one build. FIX56.1 had a memory-off
// chat still READ the saved memories (and NEXT_PUBLIC_TEMPORARY_CHAT_READS_
// MEMORY chose which of two sentences to show); chat-edge no longer does, so
// the strip no longer has a setting to mirror:
//   off       — neither uses nor saves memory; the user can switch it back on
//   temporary — neither uses nor saves memory; it cannot be switched on (the
//               server refuses), and the chat keeps its first words as its
//               name because it is never sent to a model for a title

interface Props {
  conversationId: string;
}

export function TemporaryChatBanner({ conversationId }: Props) {
  const t = useT();
  const conv = useChatStore((s) =>
    s.conversations.find((c) => c.id === conversationId),
  );
  const setMemory = useChatStore((s) => s.setConversationMemory);

  // Same default-safe read as MemoryIndicator: an absent field means
  // memory is ON, so a payload without the column can never make the
  // app claim a chat is private when it is not.
  if (!conv) return null;
  const mode = memoryModeOf(conv);
  if (mode === "on") return null;
  const temporary = mode === "temporary";

  async function turnOn() {
    try {
      await setMemory(conversationId, true);
      toast(tOutside("memory.toast.onForChat"));
    } catch {
      toast(tOutside("memory.toast.toggleFailed"));
    }
  }

  return (
    <div className="px-4">
      <div
        role="status"
        className="mx-auto flex max-w-3xl items-center gap-2 rounded-t-lg border border-b-0 border-border bg-bg-elevated px-3 py-1.5 text-[11px] text-text-secondary"
      >
        <BrainCircuit className="h-3.5 w-3.5 shrink-0 text-text-muted" />
        <span className="min-w-0 flex-1">
          <span className="font-medium text-text-primary">
            {t(temporary ? "memory.banner.title" : "memory.banner.offTitle")}
          </span>{" "}
          <span className="text-text-muted">
            {t(temporary ? "memory.banner.detail" : "memory.banner.offDetail")}
          </span>
        </span>
        {/* A temporary chat cannot have memory (chat-edge answers 409), so
            it gets no button that would only fail. */}
        {temporary ? null : (
          <button
            type="button"
            onClick={() => void turnOn()}
            className="focus-ring shrink-0 rounded px-1.5 py-0.5 font-medium text-text-secondary underline-offset-2 transition-colors hover:bg-bg-surface hover:text-text-primary hover:underline"
          >
            {t("memory.banner.turnOn")}
          </button>
        )}
      </div>
    </div>
  );
}
