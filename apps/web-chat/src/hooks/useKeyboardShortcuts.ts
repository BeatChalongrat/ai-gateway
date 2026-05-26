"use client";

// Global keyboard shortcuts for the chat surface. Lives at the
// layout level so the bindings stay registered across route
// changes. Shortcuts mirror common ChatGPT/Claude defaults:
//
//   Cmd/Ctrl + Shift + S  → toggle sidebar
//   Cmd/Ctrl + Shift + N  → new chat
//   Cmd/Ctrl + K          → focus sidebar search
//
// Each shortcut is opt-in via a handler in the options bag so a
// caller that doesn't supply ``onToggleSidebar`` simply doesn't
// bind Cmd+Shift+S. The handler is read through a ref so the
// caller can pass an inline arrow function without re-attaching
// the window listener every render.

import { useEffect, useRef } from "react";

import { useRouter } from "next/navigation";

import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

interface Options {
  onToggleSidebar?: () => void;
}

const TEXT_INPUT_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

export function useKeyboardShortcuts(opts: Options) {
  const router = useRouter();
  // Stash the handlers in a ref so the listener-attaching effect
  // only fires once on mount. Without this the `opts` default-
  // object literal recreates every render and turns the listener
  // into a leak machine.
  const toggleRef = useRef(opts.onToggleSidebar);
  toggleRef.current = opts.onToggleSidebar;

  useEffect(() => {
    if (typeof window === "undefined") return;
    function onKey(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey;
      if (!meta) return;

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

      if ((e.key === "s" || e.key === "S") && toggleRef.current) {
        e.preventDefault();
        toggleRef.current();
        return;
      }

      if (e.key === "n" || e.key === "N") {
        const target = e.target as HTMLElement | null;
        if (target && TEXT_INPUT_TAGS.has(target.tagName)) return;
        e.preventDefault();
        // Pull live state at fire-time so we don't capture a stale
        // snapshot from when the listener was first installed.
        const chatState = useChatStore.getState();
        const modelState = useModelStore.getState();
        void newChat(
          router,
          chatState.conversations,
          modelState.selectedModelId,
          chatState.createConversation,
        );
        return;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);
}

// Shared new-chat handler. Reused by the sidebar button + this
// hook so both go through the same "open the existing empty
// conversation instead of spawning a duplicate" guard.
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
  return async () => {
    const chatState = useChatStore.getState();
    const modelState = useModelStore.getState();
    await newChat(
      router,
      chatState.conversations,
      modelState.selectedModelId,
      chatState.createConversation,
    );
  };
}
