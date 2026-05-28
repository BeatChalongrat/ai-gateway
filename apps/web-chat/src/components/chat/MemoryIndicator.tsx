"use client";

// MemoryIndicator — small Brain chip in the conversation header.
//
// Two visual states driven by Conversation.memory_enabled:
//   - enabled (default): Brain glyph + "Memory" label, full opacity
//   - disabled:           BrainOff glyph + "No memory" label, dim
//
// Clicking opens a small popover with two actions:
//   - "Turn off / on for this chat"  → optimistic PATCH via store
//   - "Open Memory Settings"         → router push to /chat/memory
//
// The popover uses the same outside-click + Escape close pattern
// the Sidebar's UserMenu uses. Lives in the chat header next to
// WorkspaceButton / ConversationFiles / ExportMenu.

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Brain, BrainCircuit } from "lucide-react";

import { toast } from "@/components/ui/Toast";
import { useChatStore } from "@/stores/chatStore";

interface Props {
  conversationId: string;
}

export function MemoryIndicator({ conversationId }: Props) {
  const router = useRouter();
  const conv = useChatStore((s) =>
    s.conversations.find((c) => c.id === conversationId),
  );
  const setMemory = useChatStore((s) => s.setConversationMemory);

  // memory_enabled is optional on Conversation (older payloads).
  // Default to ON so the indicator never lies by silently showing
  // "No memory" when the backend wasn't queried for the field.
  const enabled = conv?.memory_enabled !== false;

  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function toggle() {
    if (!conv) return;
    setOpen(false);
    try {
      await setMemory(conversationId, !enabled);
      toast(
        !enabled
          ? "Memory turned on for this chat."
          : "Memory turned off for this chat.",
      );
    } catch {
      toast("Couldn't change memory setting.");
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
        title={enabled ? "Memory is active" : "Memory is off for this chat"}
        className={`focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors hover:bg-bg-elevated ${
          enabled ? "text-text-tertiary" : "text-text-muted"
        }`}
      >
        {enabled ? (
          <Brain className="h-3.5 w-3.5" />
        ) : (
          <BrainCircuit className="h-3.5 w-3.5 opacity-60" />
        )}
        <span className="hidden sm:inline">
          {enabled ? "Memory" : "No memory"}
        </span>
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="Memory settings"
          className="popover-appear absolute right-0 top-full z-30 mt-1 w-64 overflow-hidden rounded-xl border border-border bg-bg-surface shadow-2xl"
        >
          <div className="border-b border-border-subtle px-3 py-2.5">
            <div className="text-xs font-semibold text-text-primary">
              {enabled ? "Memory is on" : "Memory is off"}
            </div>
            <div className="mt-0.5 text-[11px] text-text-tertiary">
              {enabled
                ? "AI can remember facts from this conversation."
                : "Nothing said here will be saved as a memory."}
            </div>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => void toggle()}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-text-primary transition-colors hover:bg-bg-elevated"
          >
            {enabled ? (
              <BrainCircuit className="h-3.5 w-3.5 text-text-tertiary" />
            ) : (
              <Brain className="h-3.5 w-3.5 text-text-tertiary" />
            )}
            <span>{enabled ? "Turn off for this chat" : "Turn on for this chat"}</span>
          </button>
          <div className="border-t border-border-subtle" />
          <button
            type="button"
            role="menuitem"
            onClick={openSettings}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-text-primary transition-colors hover:bg-bg-elevated"
          >
            <SettingsIcon />
            <span>Open Memory Settings</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

function SettingsIcon() {
  // Same gear icon style used in the sidebar UserMenu — kept inline
  // so we don't import lucide twice for the same glyph.
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
      className="text-text-tertiary"
    >
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06A2 2 0 0 1 4.21 16.97l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9 1.65 1.65 0 0 0 4.27 7.18l-.06-.06A2 2 0 1 1 7.03 4.29l.06.06A1.65 1.65 0 0 0 8.91 4.6 1.65 1.65 0 0 0 9.91 3.09V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
