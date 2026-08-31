"use client";

// Global keyboard shortcuts for the chat surface. Lives at the
// layout level so the bindings stay registered across route
// changes. Shortcuts mirror Claude's (support.claude.com › keyboard
// shortcuts) and ChatGPT's where the two agree:
//
//   Cmd/Ctrl + Shift + S        → toggle sidebar
//   Cmd/Ctrl + Shift + N        → new chat (ours)
//   Cmd/Ctrl + Shift + O        → new chat (Claude's key — both work, FIX34)
//   Cmd/Ctrl + K                → focus sidebar search
//   Cmd/Ctrl + /  or  ?         → keyboard-shortcuts cheatsheet
//   Cmd/Ctrl + Shift + C        → copy the last response (FIX34)
//   Cmd/Ctrl + Shift + ;        → focus the composer (FIX34)
//   Cmd/Ctrl + Shift + Backspace→ delete the conversation, after a confirm (FIX34)
//   Cmd/Ctrl + Shift + V        → voice input on/off (FIX34)
//   Cmd/Ctrl + ,                → settings (FIX34)
//   Esc                         → stop generation (FIX34; an open menu/dialog wins)
//   ↑ in an EMPTY composer      → edit the last message (FIX34; lives in ChatInput)
//
// Each shortcut is opt-in via a handler in the options bag so a
// caller that doesn't supply ``onToggleSidebar`` simply doesn't
// bind Cmd+Shift+S. Handlers are read through refs so the caller can
// pass inline arrow functions without re-attaching the listener.
//
// The list in components/chat/ShortcutsDialog.tsx (SHORTCUTS) is the
// user-facing mirror of this file: a binding added here MUST be added
// there, and vice versa — the dialog must never lie.

import { useEffect, useRef } from "react";

import { useRouter } from "next/navigation";

import { toast } from "@/components/ui/Toast";
import { VOICE_TOGGLE_EVENT, lastAssistantResponse } from "@/lib/composerShortcuts";
import { tOutside } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";

interface Options {
  onToggleSidebar?: () => void;
  /** Phase 10 — open the cheatsheet (Cmd/Ctrl+/ or a bare "?"). */
  onShowShortcuts?: () => void;
  /** FIX34 — Cmd/Ctrl+Shift+Backspace: the caller shows a confirm, then deletes. */
  onDeleteConversation?: () => void;
  /** FIX34 — Cmd/Ctrl+, */
  onOpenSettings?: () => void;
}

const TEXT_INPUT_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** The composer's textarea (ChatInput tags it), else any textarea. */
export function focusComposer(): boolean {
  const el =
    document.querySelector<HTMLTextAreaElement>('textarea[data-shortcut="composer"]') ??
    document.querySelector<HTMLTextAreaElement>("textarea");
  if (!el) return false;
  el.focus();
  const end = el.value.length;
  el.setSelectionRange(end, end);
  return true;
}

/** True while a modal/menu owns the keyboard (Esc must go to it, not to "stop"). */
function dialogOpen(): boolean {
  return document.querySelector('[role="dialog"], [role="menu"], [role="listbox"]') !== null;
}

async function copyLastResponse(): Promise<void> {
  const s = useChatStore.getState();
  const id = s.activeConversationId;
  const text = id ? lastAssistantResponse(s.messages[id]) : null;
  if (!text) {
    toast(tOutside("shortcuts.nothingToCopy"));
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast(tOutside("shortcuts.copied"));
  } catch {
    toast(tOutside("shortcuts.copyFailed"));
  }
}

export function useKeyboardShortcuts(opts: Options) {
  const router = useRouter();
  // Stash the handlers in a ref so the listener-attaching effect
  // only fires once on mount. Without this the `opts` default-
  // object literal recreates every render and turns the listener
  // into a leak machine.
  const toggleRef = useRef(opts.onToggleSidebar);
  toggleRef.current = opts.onToggleSidebar;
  const shortcutsRef = useRef(opts.onShowShortcuts);
  shortcutsRef.current = opts.onShowShortcuts;
  const deleteRef = useRef(opts.onDeleteConversation);
  deleteRef.current = opts.onDeleteConversation;
  const settingsRef = useRef(opts.onOpenSettings);
  settingsRef.current = opts.onOpenSettings;

  useEffect(() => {
    if (typeof window === "undefined") return;
    function onKey(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey;

      // Esc → stop the active generation. Only when nothing else owns Esc:
      // a slash menu / canvas / dialog that handled it calls preventDefault
      // or is still open in the DOM, and then it wins.
      if (!meta && e.key === "Escape") {
        if (e.defaultPrevented || dialogOpen()) return;
        const s = useChatStore.getState();
        const id = s.activeConversationId;
        if (id && (s.abortControllers[id] || s.streamingAssistantId[id])) {
          e.preventDefault();
          void s.cancelGeneration(id);
        }
        return;
      }

      // "?" with no modifier opens the cheatsheet — but only when the
      // user is NOT typing, otherwise a question mark in a message
      // would pop a dialog mid-sentence.
      if (!meta && e.key === "?" && shortcutsRef.current) {
        const target = e.target as HTMLElement | null;
        if (target && (TEXT_INPUT_TAGS.has(target.tagName) || target.isContentEditable)) {
          return;
        }
        e.preventDefault();
        shortcutsRef.current();
        return;
      }

      if (!meta) return;

      // Cmd/Ctrl+/ works everywhere, including from inside the composer.
      if (e.key === "/" && shortcutsRef.current) {
        e.preventDefault();
        shortcutsRef.current();
        return;
      }

      // Cmd/Ctrl+, → settings (Claude, ChatGPT, every desktop app).
      if (!e.shiftKey && e.key === "," && settingsRef.current) {
        e.preventDefault();
        settingsRef.current();
        return;
      }

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

      // New chat: our Ctrl+Shift+N and Claude's Ctrl+Shift+O, both.
      if (e.key === "n" || e.key === "N" || e.key === "o" || e.key === "O") {
        e.preventDefault();
        newChat(router);
        return;
      }

      // Copy the last response.
      if (e.key === "c" || e.key === "C") {
        e.preventDefault();
        void copyLastResponse();
        return;
      }

      // Focus the composer. Shift+; is ":" on a US layout — accept both.
      if (e.key === ";" || e.key === ":" || e.code === "Semicolon") {
        if (focusComposer()) e.preventDefault();
        return;
      }

      // Delete the conversation (the caller confirms first).
      if (e.key === "Backspace" && deleteRef.current) {
        if (!useChatStore.getState().activeConversationId) return;
        e.preventDefault();
        deleteRef.current();
        return;
      }

      // Voice input on/off — the composer owns the recorder.
      if (e.key === "v" || e.key === "V") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent(VOICE_TOGGLE_EVENT));
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
