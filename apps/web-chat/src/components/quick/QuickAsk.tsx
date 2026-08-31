"use client";

// FIX34 Part A — the quick-ask window (desktop only). Rendered by app/quick/page.tsx.
//
// What Claude's quick entry does (support.claude.com › Quick entry) and what
// this page mirrors:
//   * opens on a hotkey, answer-only — no tools, so no consent card here;
//   * "New chat" is the default, with the 5 most recent chats offered to
//     continue instead;
//   * Enter sends, Esc closes (if a reply is streaming, Esc stops it first —
//     the next Esc closes);
//   * "Open in main window" hands the chat to the full app.
// A new chat started here is TEMPORARY (memory_enabled=false — a throwaway
// question); opening it in the main window turns memory back on, so it
// becomes an ordinary saved chat.
//
// Everything below reuses the store + ChatThread + ChatInput the main app
// uses; the only new transport detail is bridge.setQuickAsk(true), which makes
// every turn.submit from this page carry `quick: true` (the host answers with
// no tool list).

import { ExternalLink, MessageSquare, Plus, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ChatInput } from "@/components/chat/ChatInput";
import { ChatThread } from "@/components/chat/ChatThread";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { toast } from "@/components/ui/Toast";
import { useHostedMode } from "@/hooks/useHostedMode";
import { useIsStreaming } from "@/hooks/useStream";
import { bridge } from "@/lib/bridge";
import { hostApi } from "@/lib/hostApi";
import { tOutside, useT } from "@/lib/i18n";
import type { Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

/** Claude's quick entry offers the five most recent chats to continue. */
export const QUICK_RECENT_COUNT = 5;

/** Which recent chats the quick window offers: newest first, non-empty, 5. */
export function pickRecentConversations(all: readonly Conversation[], count = QUICK_RECENT_COUNT): Conversation[] {
  return all.filter((c) => (c.message_count ?? 0) > 0).slice(0, count);
}

export function QuickAsk() {
  const t = useT();
  const [convId, setConvId] = useState<string | null>(null);
  const conversations = useChatStore((s) => s.conversations);
  const conversationsLoaded = useChatStore((s) => s.conversationsLoaded);
  const loadConversations = useChatStore((s) => s.loadConversations);
  const loadMessages = useChatStore((s) => s.loadMessages);
  const messages = useChatStore((s) => (convId ? s.messages[convId] : undefined));
  const createConversation = useChatStore((s) => s.createConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const cancelGeneration = useChatStore((s) => s.cancelGeneration);
  const regenerate = useChatStore((s) => s.regenerate);
  const editMessage = useChatStore((s) => s.editMessage);
  const switchBranch = useChatStore((s) => s.switchBranch);
  const setConversationMemory = useChatStore((s) => s.setConversationMemory);
  const setActive = useChatStore((s) => s.setActive);
  const selectedModelId = useModelStore((s) => s.selectedModelId);
  const selectGlobalModel = useModelStore((s) => s.selectModel);
  const streaming = useIsStreaming(convId);
  const [injectedText, setInjectedText] = useState<string | null>(null);
  const creatingRef = useRef<Promise<string | null> | null>(null);

  // Tool-less turns for as long as this page is mounted.
  useEffect(() => {
    bridge.setQuickAsk(true);
    return () => bridge.setQuickAsk(false);
  }, []);

  useEffect(() => {
    if (!conversationsLoaded) void loadConversations();
  }, [conversationsLoaded, loadConversations]);

  useEffect(() => {
    if (!convId) return;
    setActive(convId);
    if (!useChatStore.getState().messagesLoaded[convId]) void loadMessages(convId);
  }, [convId, setActive, loadMessages]);

  // The window is shown by the host: put the caret in the box every time.
  useEffect(() => {
    const focusComposer = () => {
      const ta = document.querySelector<HTMLTextAreaElement>("textarea");
      ta?.focus();
    };
    focusComposer();
    window.addEventListener("focus", focusComposer);
    return () => window.removeEventListener("focus", focusComposer);
  }, [convId]);

  const startNew = useCallback(() => {
    setConvId(null);
    setActive(null);
    setInjectedText(null);
  }, [setActive]);

  // Esc: stop a running reply first; on a quiet window, close it. Ctrl+Shift+N
  // / Ctrl+Shift+O (Claude's) start a new chat. Runs at the window after the
  // composer's own handler, so a slash menu that consumed Esc wins.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented) return;
      if (e.key === "Escape") {
        e.preventDefault();
        const id = useChatStore.getState().activeConversationId;
        const live = id ? Boolean(useChatStore.getState().abortControllers[id] || useChatStore.getState().streamingAssistantId[id]) : false;
        if (id && live) void cancelGeneration(id);
        else void hostApi.quickClose();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "n" || e.key === "N" || e.key === "o" || e.key === "O")) {
        e.preventDefault();
        startNew();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancelGeneration, startNew]);

  const ensureConv = useCallback(async (): Promise<string | null> => {
    if (convId) return convId;
    if (creatingRef.current) return creatingRef.current;
    if (!selectedModelId) {
      toast(tOutside("chat.toast.pickModel"));
      return null;
    }
    const promise = (async () => {
      try {
        const fresh = await createConversation(selectedModelId, { temporary: true });
        setConvId(fresh.id);
        return fresh.id;
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("quick: createConversation failed", err);
        toast(tOutside("chat.toast.newConvFailed"));
        return null;
      } finally {
        creatingRef.current = null;
      }
    })();
    creatingRef.current = promise;
    return promise;
  }, [convId, createConversation, selectedModelId]);

  async function handleSend(text: string, fileIds: string[], imageMode?: boolean) {
    const id = await ensureConv();
    if (!id) return;
    void sendMessage(id, text, fileIds, imageMode, false);
  }

  async function openInMain() {
    if (!convId) return;
    try {
      // A quick chat is temporary; the main window keeps it, so memory comes back on.
      await setConversationMemory(convId, true);
    } catch {
      /* non-fatal — the header toggle still works in the main window */
    }
    const r = await hostApi.quickOpenInMain(convId);
    if (!r.ok) toast(tOutside("quick.openFailed"));
  }

  const recents = pickRecentConversations(conversations);
  const conv = conversations.find((c) => c.id === convId);
  const activeModel = conv?.model_id ?? selectedModelId ?? null;
  const thread = convId && messages && messages.length > 0;

  return (
    <div className="flex h-full flex-col" data-testid="quick-ask">
      {/* Title bar — the frameless window's drag handle. */}
      <header
        className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-3 py-1.5 select-none"
        style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
      >
        <span className="text-[13px] font-medium text-text-secondary">{t("quick.title")}</span>
        <span className="ml-auto flex items-center gap-1" style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}>
          {convId ? (
            <>
              <button
                type="button"
                onClick={startNew}
                className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
                title={t("quick.newChat")}
                data-testid="quick-new"
              >
                <Plus width={14} height={14} aria-hidden />
                {t("quick.newChat")}
              </button>
              <button
                type="button"
                onClick={() => void openInMain()}
                className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
                title={t("quick.openInMain")}
                data-testid="quick-open-main"
              >
                <ExternalLink width={14} height={14} aria-hidden />
                {t("quick.openInMain")}
              </button>
            </>
          ) : null}
          <button
            type="button"
            onClick={() => void hostApi.quickClose()}
            className="focus-ring rounded-md p-1 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
            aria-label={t("quick.close")}
            data-testid="quick-close"
          >
            <X width={16} height={16} aria-hidden />
          </button>
        </span>
      </header>

      {thread ? (
        <ErrorBoundary resetKey={convId}>
          <ChatThread
            convId={convId}
            messages={messages}
            onRegenerate={(messageId) => void regenerate(convId, messageId)}
            onPromptPick={(text) => setInjectedText(text)}
            onQuote={(quoted) => setInjectedText(quoted)}
            onEditMessage={(messageId, newContent) => void editMessage(convId, messageId, newContent)}
            onSwitchBranch={(messageId, branchIndex) => void switchBranch(convId, messageId, branchIndex)}
          />
        </ErrorBoundary>
      ) : (
        <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto px-3 pt-3">
          {!convId && recents.length > 0 ? (
            <div data-testid="quick-recents">
              <p className="mb-1 px-1 text-[11px] uppercase tracking-wider text-text-tertiary">{t("quick.recent")}</p>
              <ul className="flex flex-col">
                {recents.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => setConvId(c.id)}
                      className="focus-ring flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
                    >
                      <MessageSquare width={14} height={14} className="shrink-0 text-text-tertiary" aria-hidden />
                      <span className="truncate">{c.title || t("conv.untitled")}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {!convId && recents.length === 0 ? (
            <p className="px-1 py-2 text-[13px] text-text-muted">{t("quick.empty")}</p>
          ) : null}
        </div>
      )}

      <div className="shrink-0">
        <ChatInput
          key={convId ?? "draft"}
          conversationId={convId}
          disabled={false}
          streaming={streaming}
          activeModelId={activeModel}
          onModelPick={(modelId) => selectGlobalModel(modelId)}
          onSend={(text, fileIds, imageMode) => void handleSend(text, fileIds, imageMode)}
          onStop={() => {
            if (convId) void cancelGeneration(convId);
          }}
          onRequestConversation={ensureConv}
          injectedText={injectedText}
          onInjectedTextConsumed={() => setInjectedText(null)}
        />
        <p className="px-4 pb-2 text-[11px] text-text-tertiary">{t("quick.hint")}</p>
      </div>
    </div>
  );
}
