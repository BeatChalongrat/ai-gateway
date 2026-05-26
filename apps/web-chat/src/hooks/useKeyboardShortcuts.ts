"use client";

// Global keyboard shortcuts for the chat surface. Lives at the
// layout level so the bindings stay registered across route
// changes. Shortcuts mirror common ChatGPT/Claude defaults:
//
//   Cmd/Ctrl + Shift + S  → toggle sidebar
//   Cmd/Ctrl + Shift + N  → new chat
//   Cmd/Ctrl + K          → focus sidebar search
//   Escape                → close open popovers/menus (handled by
//                           individual components — we only emit a
//                           DOM event the layout listens for)
//
// Each shortcut is opt-in via a handler in the options bag so a
// caller that doesn't supply ``onToggleSidebar`` simply doesn't
// bind Cmd+Shift+S.

import { useEffect } from "react";

import { useRouter } from "next/navigation";

import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

interface Options {
  onToggleSidebar?: () => void;
}

const TEXT_INPUT_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

export function useKeyboardShortcuts(opts: Options = {}) {
  const router = useRouter();
  const createConversation = useChatStore((s) => s.createConversation);
  const conversations = useChatStore((s) => s.conversations);
  const selectedModelId = useModelStore((s) => s.selectedModelId);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey;
      if (!meta) return;

      // Cmd/Ctrl + K focuses the sidebar search even when the user
      // is typing in another input — that's the conventional global
      // command-palette binding. The sidebar tags its search field
      // with data-shortcut="conv-search" so this stays decoupled.
      if (!e.shiftKey && (e.key === "k" || e.key === "K")) {
        const el = document.querySelector<HTMLInputElement>(
          'input[data-shortcut="conv-search"]',
        );
        if (el) {
          e.preventDefault();
          el.focus();
          el.select();
        }
        return;
      }

      if (!e.shiftKey) return;

      // Toggle sidebar — Cmd/Ctrl + Shift + S.
      if ((e.key === "s" || e.key === "S") && opts.onToggleSidebar) {
        e.preventDefault();
        opts.onToggleSidebar();
        return;
      }

      // New chat — Cmd/Ctrl + Shift + N. Skipped when the cursor is
      // in a text field so the user's text doesn't get hijacked.
      if (e.key === "n" || e.key === "N") {
        const target = e.target as HTMLElement | null;
        if (target && TEXT_INPUT_TAGS.has(target.tagName)) return;
        e.preventDefault();
        void newChat(
          router,
          conversations,
          selectedModelId,
          createConversation,
        );
        return;
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [opts, router, createConversation, conversations, selectedModelId]);
}

// Shared new-chat handler. Reused by the sidebar button + this
// hook so both go through the same "open the existing empty
// conversation instead of spawning a duplicate" guard. The
// chatStore exposes the conversations list as a ChatGPT-style
// pinned-to-the-top sorted view; the first item is the most
// recently updated, which is the natural reuse candidate.
async function newChat(
  router: ReturnType<typeof useRouter>,
  conversations: ReturnType<typeof useChatStore.getState>["conversations"],
  selectedModelId: string | null,
  createConversation: ReturnType<typeof useChatStore.getState>["createConversation"],
) {
  if (!selectedModelId) return;
  // Reuse a freshly-created empty conversation if one exists at the
  // top of the list. Heuristic: message_count === 0 AND it was
  // updated in the last 60 seconds AND uses the currently-selected
  // model. Avoids the "spam New chat → 10 empty rows" bug.
  const sixtySecondsAgo = Date.now() - 60_000;
  const candidate = conversations.find((c) => {
    if (c.message_count !== 0) return false;
    if (c.model_id !== selectedModelId) return false;
    const t = new Date(c.updated_at).getTime();
    return !Number.isNaN(t) && t > sixtySecondsAgo;
  });
  if (candidate) {
    router.push(`/chat/${candidate.id}`);
    return;
  }
  const fresh = await createConversation(selectedModelId);
  router.push(`/chat/${fresh.id}`);
}

// Exported because the Sidebar's "+ New chat" button uses the same
// dedup heuristic. Pulling it here keeps the rule in one place.
export function useNewChatHandler() {
  const router = useRouter();
  const createConversation = useChatStore((s) => s.createConversation);
  const conversations = useChatStore((s) => s.conversations);
  const selectedModelId = useModelStore((s) => s.selectedModelId);
  return async () => {
    await newChat(router, conversations, selectedModelId, createConversation);
  };
}
