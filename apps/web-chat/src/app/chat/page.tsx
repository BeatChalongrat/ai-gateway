"use client";

// /chat landing page — ChatGPT / Claude style.
//
// Renders the personalised greeting + category chips + the same
// rounded-pill input you get inside a conversation. The conversation
// is created lazily — only when the user actually sends OR attaches
// their first file. Click "+ New chat" and walk away → nothing in
// the sidebar (the conversation list view filters empties).
//
// FIX126 F2 — an attach creates the conversation (the upload endpoint is
// POST /conversations/{id}/files, and starting the upload at attach time is
// what lets the server read a long document while the person types). If
// nothing is sent, that row is DELETED: when the person leaves this page,
// when the tab is closed or reloaded (keepalive request), and when they
// remove every attachment. A send marks the draft as used first, so the
// conversation it went to is never touched.
//
// Note: we deliberately do NOT auto-redirect to the most recent
// existing conversation. The redesigned UX wants a clean landing
// surface (matches ChatGPT's "Where should we begin?" + Claude's
// "Beat returns!"), not "open whatever you were working on last".

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";
import { api } from "@/lib/api";
import { HOSTED_MODE } from "@/lib/auth";
import { hostApi } from "@/lib/hostApi";
import { ChatInput, DRAFT_CONV_KEY, uploadFiles } from "@/components/chat/ChatInput";
import { EmptyState } from "@/components/chat/EmptyState";
import { ProjectChip } from "@/components/chat/ProjectChip";
import { toast } from "@/components/ui/Toast";
import { SEND_STRANDED_EVENT } from "@/components/ui/ErrorBanner";
import { clearDraft, NEW_CHAT_DRAFT_KEY } from "@/lib/draftStore";
import { consumeQueryPrefill } from "@/lib/deepLinkParams";
import { tOutside } from "@/lib/i18n";
import { gateImagesForVision } from "@/lib/visionGate";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

export default function ChatLanding() {
  const router = useRouter();
  const createConversation = useChatStore((s) => s.createConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const draftChipCount = useChatStore(
    (s) => s.pendingFiles[DRAFT_CONV_KEY]?.length ?? 0,
  );
  const sendMessage = useChatStore((s) => s.sendMessage);
  // FIX132 F30 — set by "แชทใหม่ในโปรเจกต์นี้"; the first send files the chat there.
  const nextProjectId = useChatStore((s) => s.nextConversationProjectId);
  const setNextProjectId = useChatStore((s) => s.setNextConversationProjectId);
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

  // FIX34 Part B — sran://…/new?q=… lands here as /chat?q=…: the text is put
  // in the composer and NOTHING is sent (Anthropic: "Prompts are prefilled but
  // not automatically sent" — otherwise any web page could make the app ask
  // anything with one click). The param is removed from the address bar.
  useEffect(() => {
    const q = consumeQueryPrefill();
    if (q) setInjectedText(q);
  }, []);

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
  // FIX126 F2 — set the instant a send starts: from then on the draft
  // conversation belongs to that message and is never discarded.
  const sentRef = useRef(false);

  // Delete the eager-created conversation if nothing was sent into it. Waits
  // for a create still in flight so a row that lands after the person left
  // is removed too. Idempotent: the ref is cleared before the request.
  const discardDraftConv = useCallback(async () => {
    if (sentRef.current) return;
    if (creatingRef.current) {
      try {
        await creatingRef.current;
      } catch {
        /* create failed → nothing to delete */
      }
    }
    if (sentRef.current) return;
    const id = draftConvIdRef.current;
    if (!id) return;
    draftConvIdRef.current = null;
    try {
      await deleteConversation(id);
    } catch {
      /* best effort — the sidebar hides a conversation with no messages */
    }
  }, [deleteConversation]);

  // Every attachment removed without sending → the conversation they were
  // uploaded into goes too. (A send also empties the bucket, after sentRef.)
  const prevChipCountRef = useRef(draftChipCount);
  useEffect(() => {
    const prev = prevChipCountRef.current;
    prevChipCountRef.current = draftChipCount;
    if (prev > 0 && draftChipCount === 0) void discardDraftConv();
  }, [draftChipCount, discardDraftConv]);

  // Leaving the page (in-app navigation → unmount; tab close / reload →
  // pagehide) without sending discards the draft and its chips.
  const discardRef = useRef(discardDraftConv);
  discardRef.current = discardDraftConv;
  useEffect(() => {
    const onPageHide = () => {
      if (sentRef.current) return;
      const id = draftConvIdRef.current;
      if (!id) return;
      draftConvIdRef.current = null;
      // FIX127 C3 — Electron refuses the keepalive cross-origin DELETE
      // while the renderer is torn down; the host does it over IPC.
      if (HOSTED_MODE) hostApi.discardEmptyConversation(id);
      else api.deleteConversationOnExit(id);
    };
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      if (sentRef.current) return;
      if (!draftConvIdRef.current && !creatingRef.current) return;
      // The chips point at the conversation being deleted; do not let
      // the next new-chat page offer them again.
      useChatStore.getState().clearPendingFiles(DRAFT_CONV_KEY);
      void discardRef.current();
    };
  }, []);

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

  // FIX127 A3 — the banner's "send in a new chat": the text of a send that
  // was refused because its conversation had been deleted elsewhere. The
  // draft is already in the composer; this sends it without retyping.
  const handleSendRef = useRef<(t: string, f: string[]) => Promise<void>>(async () => {});
  // FIX132 F25 — the text goes out on the model of the chat it was meant
  // for (NIGHT3 j5-05: Deepseek V4 Pro became อัตโนมัติ). Picking it is the
  // same as picking it in the model menu; the send waits for the render
  // that carries the pick, since ensureConv/handleSend read it from there.
  const [strandedToSend, setStrandedToSend] = useState<string | null>(null);
  useEffect(() => {
    const onStranded = () => {
      const st = useChatStore.getState();
      const stranded = st.strandedSend;
      if (!stranded?.text) return;
      st.clearStrandedSend();
      st.setError(null);
      clearDraft(NEW_CHAT_DRAFT_KEY);
      if (stranded.modelId) selectGlobalModel(stranded.modelId);
      setStrandedToSend(stranded.text);
    };
    window.addEventListener(SEND_STRANDED_EVENT, onStranded);
    return () => window.removeEventListener(SEND_STRANDED_EVENT, onStranded);
  }, [selectGlobalModel]);
  useEffect(() => {
    if (strandedToSend === null) return;
    setStrandedToSend(null);
    void handleSendRef.current(strandedToSend, []);
  }, [strandedToSend]);

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
    // FIX126 F2 — synchronous, before the composer clears its chips.
    sentRef.current = true;
    // Attaching a file already created the draft conv; reuse it so
    // the upload + the send target the same row. When the user
    // types-and-sends without attaching, this falls through to a
    // fresh create at first send (the original "lazy" behaviour).
    const id = await ensureConv();
    if (!id) {
      sentRef.current = false;
      return;
    }
    // Kick off the stream BEFORE navigating so the assistant
    // placeholder mounts inside chatStore by the time the
    // conversation page renders. The page sees messages already
    // streaming via the store; no extra hand-off needed.
    void sendMessage(id, text, fileIds, imageMode, videoMode);
    router.push(`/chat/${id}`);
  }
  handleSendRef.current = handleSend;

  return (
    <AttachmentDropZone
      className="flex flex-1 flex-col overflow-hidden"
      enabled
      onFiles={(filesRaw) => {
        // W3 — gate BEFORE ensureConv so a rejected image-only drop
        // never eager-creates an empty conversation row.
        const files = gateImagesForVision(filesRaw, selectedModelId);
        if (files.length === 0) return;
        // Chip bucket is DRAFT_CONV_KEY so the dropped-file chip
        // appears in the landing-page ChatInput (which renders
        // from that bucket because conversationId is null here).
        // FIX126 F1 — uploadFiles applies the per-message cap and stages
        // the chips BEFORE it asks ensureConv for the network target, so
        // a drop that is entirely over the limit creates nothing.
        void uploadFiles(
          ensureConv,
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
          {/* FIX132 F30 — "แชทใหม่ในโปรเจกต์นี้" says so here, with a way out. */}
          {nextProjectId ? (
            <div className="mb-2 flex justify-center">
              <ProjectChip projectId={nextProjectId} variant="newChat" onClear={() => setNextProjectId(null)} />
            </div>
          ) : null}
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
