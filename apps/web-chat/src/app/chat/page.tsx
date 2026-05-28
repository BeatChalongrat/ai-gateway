"use client";

// /chat landing page — ChatGPT / Claude style.
//
// Renders the personalised greeting + category chips + the same
// rounded-pill input you get inside a conversation. The conversation
// is created lazily — only when the user actually sends OR attaches
// their first file. Click "+ New chat" and walk away → nothing in
// the sidebar (the conversation list view filters empties).
//
// Note: we deliberately do NOT auto-redirect to the most recent
// existing conversation. The redesigned UX wants a clean landing
// surface (matches ChatGPT's "Where should we begin?" + Claude's
// "Beat returns!"), not "open whatever you were working on last".

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";
import { ChatInput, uploadFiles } from "@/components/chat/ChatInput";
import { EmptyState } from "@/components/chat/EmptyState";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

export default function ChatLanding() {
  const router = useRouter();
  const createConversation = useChatStore((s) => s.createConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const selectedModelId = useModelStore((s) => s.selectedModelId);
  const selectGlobalModel = useModelStore((s) => s.selectModel);
  const addPendingFile = useChatStore((s) => s.addPendingFile);
  const updateFileProgress = useChatStore((s) => s.updateFileProgress);
  const markFileReady = useChatStore((s) => s.markFileReady);
  const markFileError = useChatStore((s) => s.markFileError);

  // Landing text is buffered locally so the user sees what they
  // typed survive the navigation into the new conversation.
  const [injectedText, setInjectedText] = useState<string | null>(null);

  // Eager-created conversation id. Starts null; the first attach /
  // paste / drop / send promotes it to a real uuid via ensureConv.
  // Stored in a ref so the synchronous attach handler can read it
  // without racing the React setState batch.
  const [draftConvId, setDraftConvId] = useState<string | null>(null);
  const draftConvIdRef = useRef<string | null>(null);
  const creatingRef = useRef<Promise<string | null> | null>(null);

  // ensureConv — single source of truth for "the conversation this
  // landing page is editing". Reuses an existing draft, otherwise
  // creates one (with the currently-selected model id) and stores
  // it locally. The first concurrent caller wins; later callers
  // await the same promise so we never create two empty rows from
  // two near-simultaneous events (e.g. paste + click).
  const ensureConv = useCallback(async (): Promise<string | null> => {
    if (draftConvIdRef.current) return draftConvIdRef.current;
    if (creatingRef.current) return creatingRef.current;
    if (!selectedModelId) return null;
    const promise = (async () => {
      try {
        const fresh = await createConversation(selectedModelId);
        draftConvIdRef.current = fresh.id;
        setDraftConvId(fresh.id);
        return fresh.id;
      } catch {
        return null;
      } finally {
        creatingRef.current = null;
      }
    })();
    creatingRef.current = promise;
    return promise;
  }, [createConversation, selectedModelId]);

  async function handleSend(text: string, fileIds: string[]) {
    if (!selectedModelId) return;
    // Attaching a file already created the draft conv; reuse it so
    // the upload + the send target the same row. When the user
    // types-and-sends without attaching, this falls through to a
    // fresh create at first send (the original "lazy" behaviour).
    const id = await ensureConv();
    if (!id) return;
    // Kick off the stream BEFORE navigating so the assistant
    // placeholder mounts inside chatStore by the time the
    // conversation page renders. The page sees messages already
    // streaming via the store; no extra hand-off needed.
    void sendMessage(id, text, fileIds);
    router.push(`/chat/${id}`);
  }

  return (
    <AttachmentDropZone
      className="flex flex-1 flex-col overflow-hidden"
      enabled
      onFiles={async (files) => {
        const id = await ensureConv();
        if (!id) return;
        void uploadFiles(id, files, {
          addPendingFile,
          updateFileProgress,
          markFileReady,
          markFileError,
        });
      }}
    >
      <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto">
        <EmptyState onPrompt={(text) => setInjectedText(text)}>
          <ChatInput
            conversationId={draftConvId}
            disabled={false}
            streaming={false}
            activeModelId={selectedModelId ?? null}
            onModelPick={(modelId) => selectGlobalModel(modelId)}
            onSend={(t, fileIds) => void handleSend(t, fileIds)}
            onStop={() => undefined}
            onRequestConversation={ensureConv}
            injectedText={injectedText}
            onInjectedTextConsumed={() => setInjectedText(null)}
          />
        </EmptyState>
      </div>
    </AttachmentDropZone>
  );
}
