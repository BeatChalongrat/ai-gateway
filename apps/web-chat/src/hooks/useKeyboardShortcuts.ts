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
        newChat(router);
        return;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);
}

// Shared new-chat handler. Reused by the sidebar button + this
// hook so the shortcut and the button stay in lockstep.
//
// ChatGPT-style lazy create: navigate to the landing page and clear
// the active selection. NO conversation is created until the user
// actually sends their first message (handled in /chat/page.tsx).
// This keeps the sidebar free of empty "New conversation" rows from
// users who click "+ New chat" and then change their mind.
function newChat(router: ReturnType<typeof useRouter>) {
  useChatStore.getState().setActive(null);
  router.push("/chat");
}

// Exported because the Sidebar's "+ New chat" button uses the same
// handler. Kept here so the keyboard shortcut and the button can
// never drift out of sync.
export function useNewChatHandler() {
  const router = useRouter();
  return () => {
    newChat(router);
  };
}
