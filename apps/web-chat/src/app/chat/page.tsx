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
import { ChatInput, DRAFT_CONV_KEY, uploadFiles } from "@/components/chat/ChatInput";
import { EmptyState } from "@/components/chat/EmptyState";
import { toast } from "@/components/ui/Toast";
import { tOutside } from "@/lib/i18n";
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
  const setFileExtractionStatus = useChatStore(
    (s) => s.setFileExtractionStatus,
  );

  // Landing text is buffered locally so the user sees what they
  // typed survive the navigation into the new conversation.
  const [injectedText, setInjectedText] = useState<string | null>(null);

  // Eager-created conversation id. Stored ONLY in a ref — not in
  // React state — so the landing-page ChatInput never re-renders
  // with a real conversationId once ensureConv resolves. Why: the
  // chips ChatInput renders are keyed by `conversationId ?? DRAFT`,
  // so if conversationId flipped from null to a real id mid-upload,
  // the selector would switch buckets and the chip would disappear.
  // Keeping conversationId={null} for the landing instance means
  // the chips live in the DRAFT bucket for the entire landing
  // lifetime; after submit the page navigates away and a fresh
  // ChatInput mounts on the conv page with the real id.
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
    if (!selectedModelId) {
      // Most common reason a fresh user sees "nothing happens" when
      // clicking + on the landing page: the model catalog hasn't
      // finished loading yet (loadModels is async, no localStorage
      // pick on first visit). Surface it so the user understands
      // they're not stuck on a broken button.
      toast(tOutside("chat.toast.pickModelFiles"));
      return null;
    }
    const promise = (async () => {
      try {
        const fresh = await createConversation(selectedModelId);
        draftConvIdRef.current = fresh.id;
        return fresh.id;
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("chat/landing: createConversation failed", err);
        toast(tOutside("chat.toast.newConvFailed"));
        return null;
      } finally {
        creatingRef.current = null;
      }
    })();
    creatingRef.current = promise;
    return promise;
  }, [createConversation, selectedModelId]);

  async function handleSend(
    text: string,
    fileIds: string[],
    imageMode?: boolean,
    videoMode?: boolean,
  ) {
    if (!selectedModelId) {
      toast(tOutside("chat.toast.pickModel"));
      return;
    }
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
    void sendMessage(id, text, fileIds, imageMode, videoMode);
    router.push(`/chat/${id}`);
  }

  return (
    <AttachmentDropZone
      className="flex flex-1 flex-col overflow-hidden"
      enabled
      onFiles={async (files) => {
        const id = await ensureConv();
        if (!id) return;
        // Chip bucket is DRAFT_CONV_KEY so the dropped-file chip
        // appears in the landing-page ChatInput (which renders
        // from that bucket because conversationId is null here).
        // Network target is the eager-created conv id.
        void uploadFiles(
          id,
          files,
          {
            addPendingFile,
            updateFileProgress,
            markFileReady,
            markFileError,
            setFileExtractionStatus,
          },
          DRAFT_CONV_KEY,
        );
      }}
    >
      <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto">
        <EmptyState onPrompt={(text) => setInjectedText(text)}>
          <ChatInput
            // Intentionally always null: keeps ChatInput's chip
            // bucket constant (DRAFT) for this instance's lifetime
            // so files staged before ensureConv resolves stay
            // visible without a render-time bucket flip. ensureConv
            // (passed via onRequestConversation) provides the real
            // id to the upload + the send paths.
            conversationId={null}
            disabled={false}
            streaming={false}
            activeModelId={selectedModelId ?? null}
            onModelPick={(modelId) => selectGlobalModel(modelId)}
            onSend={(t, fileIds, imageMode, videoMode) =>
              void handleSend(t, fileIds, imageMode, videoMode)
            }
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
