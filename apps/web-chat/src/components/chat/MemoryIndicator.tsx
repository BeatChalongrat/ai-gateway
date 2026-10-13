"use client";

// MemoryIndicator — small Brain chip in the conversation header.
//
// Three states (FIX130 A2 — lib/memoryMode.ts, the one rule):
//   - on (default):  Brain glyph + "Memory" label
//   - off:           BrainOff glyph (the Brain, struck through) + "Memory off", dim
//   - temporary:     MessageSquareDashed glyph (a dashed bubble) + "Temporary chat", dim
// AM19 — one icon per meaning: BrainCircuit is the composer's thinking toggle
// and nothing else; the two no-memory states each have a glyph of their own.
// The popover says what each state really does — off and temporary both
// neither use nor save memory — and offers:
//   - "Turn off / on for this chat"  → optimistic PATCH via store
//                                      (not on a temporary chat: the server
//                                      refuses memory there, so no button)
//   - "Open Memory Settings"         → router push to /chat/memory
//
// The popover uses the same outside-click + Escape close pattern
// the Sidebar's UserMenu uses. Lives in the chat header next to
// WorkspaceButton / ConversationFiles / ExportMenu.

import { useCallback, useRef, useState } from "react";
import { useDismissable } from "@/hooks/useDismissable";
import { useRouter } from "next/navigation";

import { Brain, MessageSquareDashed, Settings } from "lucide-react";

import { BrainOff } from "@/components/ui/BrainOff";

import { toast } from "@/components/ui/Toast";
import { tOutside, useT } from "@/lib/i18n";
import { useMemoryPendingStore } from "@/hooks/useMemoryPendingCount";
import { memoryModeOf } from "@/lib/memoryMode";
import { useChatStore } from "@/stores/chatStore";

interface Props {
  conversationId: string;
}

export function MemoryIndicator({ conversationId }: Props) {
  const t = useT();
  const router = useRouter();
  const conv = useChatStore((s) =>
    s.conversations.find((c) => c.id === conversationId),
  );
  const setMemory = useChatStore((s) => s.setConversationMemory);

  // memory_enabled is optional on Conversation (older payloads).
  // Default to ON so the indicator never lies by silently showing
  // "Memory off" when the backend wasn't queried for the field.
  const mode = memoryModeOf(conv);
  const enabled = mode === "on";
  const temporary = mode === "temporary";

  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  // S8 — small blue dot when the user has unreviewed preference
  // suggestions. Subscribes to the same store the sidebar badge
  // reads; the chat layout's useMemoryPendingPoll keeps it fresh.
  const pendingCount = useMemoryPendingStore((s) => s.count);

  // FIX84 row 3 — outside-click AND Escape, through the one shared rule.
  const closePopover = useCallback(() => setOpen(false), []);
  useDismissable(wrapRef, open, closePopover);

  async function toggle() {
    if (!conv || temporary) return;
    setOpen(false);
    try {
      await setMemory(conversationId, !enabled);
      toast(
        !enabled
          ? tOutside("memory.toast.onForChat")
          : tOutside("memory.toast.offForChat"),
      );
    } catch {
      toast(tOutside("memory.toast.toggleFailed"));
    }
  }

  function openSettings() {
    setOpen(false);
    router.push("/chat/memory");
  }

  if (!conv) return null;

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={
          enabled
            ? pendingCount > 0
              ? t("memory.indicator.onPendingTooltip", { n: String(pendingCount) })
              : t("memory.indicator.onTooltip")
            : temporary
              ? t("memory.indicator.temporaryTooltip")
              : t("memory.indicator.offTooltip")
        }
        className={`focus-ring relative inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors hover:bg-bg-elevated ${
          enabled ? "text-text-muted" : "text-text-muted"
        }`}
      >
        {enabled ? (
          <Brain className="h-3.5 w-3.5" />
        ) : temporary ? (
          <MessageSquareDashed className="h-3.5 w-3.5 opacity-60" />
        ) : (
          <BrainOff className="h-3.5 w-3.5 opacity-60" />
        )}
        <span className="hidden sm:inline">
          {enabled
            ? t("memory.indicator.on")
            : temporary
              ? t("memory.indicator.temporary")
              : t("memory.indicator.off")}
        </span>
        {/* S8 — small blue dot when pending suggestions exist.
            Tiny + absolute so it overlays the Brain icon without
            reflowing the row's width. */}
        {pendingCount > 0 ? (
          <span
            aria-hidden
            className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-blue-500 ring-2 ring-bg-base"
          />
        ) : null}
      </button>

      {open ? (
        <div
          role="menu"
          aria-label={t("a11y.memorySettings")}
          className="popover-appear absolute right-0 top-full z-30 mt-1 w-64 overflow-hidden rounded-xl border border-border bg-bg-surface shadow-2xl"
        >
          <div className="border-b border-border-subtle px-3 py-2.5">
            <div className="text-xs font-semibold text-text-primary">
              {enabled
                ? t("memory.popover.on")
                : temporary
                  ? t("memory.popover.temporary")
                  : t("memory.popover.off")}
            </div>
            <div className="mt-0.5 text-[11px] text-text-muted">
              {enabled
                ? t("memory.popover.onHint")
                : temporary
                  ? t("memory.popover.temporaryHint")
                  : t("memory.popover.offHint")}
            </div>
          </div>
          {temporary ? null : (
            <>
              <button
                type="button"
                role="menuitem"
                onClick={() => void toggle()}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-text-primary transition-colors hover:bg-bg-elevated"
              >
                {enabled ? (
                  <BrainOff className="h-3.5 w-3.5 text-text-muted" />
                ) : (
                  <Brain className="h-3.5 w-3.5 text-text-muted" />
                )}
                <span>{enabled ? t("memory.popover.toggleOff") : t("memory.popover.toggleOn")}</span>
              </button>
              <div className="border-t border-border-subtle" />
            </>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={openSettings}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-text-primary transition-colors hover:bg-bg-elevated"
          >
            <Settings width={14} height={14} className="text-text-muted" aria-hidden />
            <span>{t("memory.openSettings")}</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}
