"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";
import { ChatInput, uploadFiles } from "@/components/chat/ChatInput";
import { ChatThread } from "@/components/chat/ChatThread";
import { ConversationFiles } from "@/components/chat/ConversationFiles";
import { EmptyState } from "@/components/chat/EmptyState";
import { ExportMenu } from "@/components/chat/ExportMenu";
import { MemoryIndicator } from "@/components/chat/MemoryIndicator";
import { MessageQueue } from "@/components/chat/MessageQueue";
import { TemporaryChatBanner } from "@/components/chat/TemporaryChatBanner";
import { ContextMeter } from "@/components/chat/ContextMeter";
import { ShareModal } from "@/components/chat/ShareModal";
import { FilePreviewPane } from "@/components/chat/FilePreviewModal";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { WorkspaceButton } from "@/components/workspace/WorkspaceButton";
import { WorkspacePanel } from "@/components/workspace/WorkspacePanel";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { MessageThreadSkeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { tOutside } from "@/lib/i18n";
import { useIsStreaming } from "@/hooks/useStream";
import { ApiError, api } from "@/lib/api";
import { consumeQueryAttach, consumeQueryPrefill } from "@/lib/deepLinkParams";
import { gateImagesForVision } from "@/lib/visionGate";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";
import { useSidePaneStore } from "@/stores/sidePaneStore";

export default function ConversationPage() {
  const params = useParams();
  const router = useRouter();
  const convId =
    typeof params?.conversationId === "string" ? params.conversationId : null;

  const conversations = useChatStore((s) => s.conversations);
  const messages = useChatStore((s) =>
    convId ? s.messages[convId] : undefined,
  );
  // Discriminator — flips to true the moment loadMessages settles
  // (success OR failure) so the page can tell "fetch not done yet"
  // from "fetch done, conversation is empty". Without this the
  // EmptyState branch would render during the in-flight window,
  // flickering the centred greeting before the messages arrive.
  const messagesLoaded = useChatStore((s) =>
    convId ? s.messagesLoaded[convId] === true : false,
  );
  const setActive = useChatStore((s) => s.setActive);
  const loadMessages = useChatStore((s) => s.loadMessages);
  const loadOlderMessages = useChatStore((s) => s.loadOlderMessages);
  const hasMoreOlder = useChatStore((s) =>
    convId ? s.hasMoreMessages[convId] : undefined,
  );
  const loadingOlder = useChatStore((s) =>
    convId ? s.loadingOlder[convId] : false,
  );
  const sendMessage = useChatStore((s) => s.sendMessage);
  const cancelGeneration = useChatStore((s) => s.cancelGeneration);
  const regenerate = useChatStore((s) => s.regenerate);
  const setConversationModel = useChatStore((s) => s.setConversationModel);
  const setConversationKB = useChatStore((s) => s.setConversationKB);
  const editMessage = useChatStore((s) => s.editMessage);
  const switchBranch = useChatStore((s) => s.switchBranch);
  const addPendingFile = useChatStore((s) => s.addPendingFile);
  const updateFileProgress = useChatStore((s) => s.updateFileProgress);
  const markFileReady = useChatStore((s) => s.markFileReady);
  const markFileError = useChatStore((s) => s.markFileError);
  const setFileExtractionStatus = useChatStore(
    (s) => s.setFileExtractionStatus,
  );
  const selectGlobalModel = useModelStore((s) => s.selectModel);
  const getModelById = useModelStore((s) => s.getModelById);

  const streaming = useIsStreaming(convId);
  const conv = conversations.find((c) => c.id === convId);

  const [injectedText, setInjectedText] = useState<string | null>(null);
  const [shareOpen, setShareOpen] = useState(false);

  // FIX34 Part B/C — deep-link parameters on a conversation route:
  //   ?q=…      prefill the composer (never sent);
  //   ?attach=… a file the desktop host already uploaded to THIS conversation
  //             (the Explorer menu) — staged as a ready chip so the user sees
  //             what is attached and decides whether to send.
  useEffect(() => {
    if (!convId) return;
    const q = consumeQueryPrefill();
    if (q) setInjectedText(q);
    const attach = consumeQueryAttach();
    if (!attach) return;
    void (async () => {
      try {
        const res = await api.listFiles(convId);
        const row = res.files.find((f) => f.id === attach);
        if (!row) return;
        const placeholder = new File([], row.filename, { type: row.content_type });
        // The chip shows the real size; the bytes already live on the server.
        Object.defineProperty(placeholder, "size", { value: row.file_size_bytes });
        const pendingId = addPendingFile(convId, placeholder);
        markFileReady(convId, pendingId, row.id, row.extraction_status);
      } catch {
        /* the file is still listed under the conversation's files */
      }
    })();
  }, [convId, addPendingFile, markFileReady]);
  // FIX76 — the one side-pane slot (canvas / workspace / preview) is page
  // state in sidePaneStore. This page is its host: it announces itself so
  // message cards open previews in the pane, and it empties the slot when
  // the conversation changes or the page unmounts (a deleted conversation
  // leaves this page, so that case is covered too).
  const pane = useSidePaneStore((s) => s.pane);
  const openPane = useSidePaneStore((s) => s.open);
  const closePane = useSidePaneStore((s) => s.close);
  const closePaneIf = useSidePaneStore((s) => s.closeIf);
  const setPaneHostMounted = useSidePaneStore((s) => s.setHostMounted);
  useEffect(() => {
    setPaneHostMounted(true);
    return () => {
      closePane();
      setPaneHostMounted(false);
    };
  }, [convId, closePane, setPaneHostMounted]);

  // setActive + lazy loadMessages on convId change. We read the
  // current messages slot via getState() rather than subscribing
  // here so the effect doesn't re-run during streaming (every
  // content_delta would otherwise re-trigger it).
  //
  // The previous version used a 150ms ``skeletonHeld`` latch with a
  // setTimeout cleanup — when messages arrived (deps changed) the
  // effect re-ran with messages defined, the cleanup cancelled the
  // pending timeout, and skeletonHeld stayed true forever, leaving
  // the page stuck on the skeleton. The fix is to make the skeleton
  // a pure function of "messages still undefined".
  useEffect(() => {
    if (!convId) return;
    setActive(convId);
    // Gate the load on the discriminator, not on the messages slot.
    // The slot is now seeded with ``[]`` on error so a follow-up
    // visit would skip the retry if we only checked ``undefined``.
    if (!useChatStore.getState().messagesLoaded[convId]) {
      void loadMessages(convId);
    }
  }, [convId, setActive, loadMessages]);

  // Conversation-gone escape hatch: handleConversationGone (404/410 —
  // deleted elsewhere) clears activeConversationId while this route
  // still points at the dead id, which would otherwise leave the page
  // parked on the skeleton forever. Redirect to the new-chat page —
  // but only after this conversation was actually observed active, so
  // the initial null-before-setActive render can't trigger a premature
  // redirect (and replace() unmounts the page, so no loop).
  const activeId = useChatStore((s) => s.activeConversationId);
  const wasActiveRef = useRef(false);
  useEffect(() => {
    if (!convId) return;
    if (activeId === convId) {
      wasActiveRef.current = true;
      return;
    }
    if (wasActiveRef.current && activeId === null) {
      router.replace("/chat");
    }
  }, [activeId, convId, router]);

  if (!convId) {
    router.replace("/chat");
    return null;
  }
  // Skeleton renders ONLY when the fetch has not yet completed.
  // Once messagesLoaded[convId] flips to true the page falls through
  // to the empty-vs-thread branch below — even if the load failed,
  // because the errorBanner already surfaces the failure.
  // FIX127 D4 — a message deep link: /chat/<id>#msg-<messageId> opens the
  // chat on that message (centred, briefly highlighted). Once per hash,
  // after the rows exist.
  const jumpedHashRef = useRef<string | null>(null);
  useEffect(() => {
    if (typeof window === "undefined" || !messagesLoaded || !messages?.length) return;
    const hash = window.location.hash;
    if (!hash.startsWith("#msg-") || jumpedHashRef.current === hash) return;
    const el = document.getElementById(hash.slice(1));
    if (!el) return;
    jumpedHashRef.current = hash;
    el.scrollIntoView({ block: "center" });
    el.classList.add("deep-link-target");
    const t = window.setTimeout(() => el.classList.remove("deep-link-target"), 2000);
    return () => window.clearTimeout(t);
  }, [messagesLoaded, messages]);

  // FIX127 D4 — regenerated answers are branches (see the thread prop below).
  // Before the early return: a hook.
  const visibleMessages = useMemo(() => (messages ?? []).filter((m) => m.status !== "regenerated"), [messages]);
  if (!messagesLoaded || messages === undefined) {
    return (
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="mx-auto w-full max-w-3xl flex-1 overflow-y-auto px-4 py-6 md:px-10">
          <MessageThreadSkeleton />
        </div>
      </div>
    );
  }

  // Shared input element — same props whether we render it
  // centred inside EmptyState (empty conversation) or bottom-
  // anchored under ChatThread (regular conversation). Factoring it
  // out keeps both render branches honest about feature parity.
  const composer = (
    <ChatInput
      conversationId={convId}
      disabled={false}
      streaming={streaming}
      activeModelId={conv?.model_id ?? null}
      onModelPick={async (modelId) => {
        if (!convId) return;
        // Update the global default in the same step so the next
        // conversation the user opens inherits this pick. Runs
        // BEFORE the server round-trip so the pill flips
        // immediately if the network is slow; the chatStore action
        // also does an optimistic update of conv.model_id, so the
        // visible state is consistent without waiting on the PATCH.
        selectGlobalModel(modelId);
        try {
          await setConversationModel(convId, modelId);
          const label =
            modelId === AUTO_MODEL_ID
              ? "Auto"
              : getModelById(modelId)?.name ?? modelId;
          toast(tOutside("chat.toast.modelSwitched", { label }));
        } catch (err) {
          // Surface the SERVER's reason rather than a generic
          // "Couldn't change model" — covers "model not allowed",
          // "conversation not found", auth errors, etc. Falls back
          // to the generic message when the error wasn't an
          // ApiError (network glitch, bug).
          // eslint-disable-next-line no-console
          console.error("setConversationModel failed", err);
          const msg =
            err instanceof ApiError && err.message
              ? tOutside("chat.toast.modelChangeFailedWith", { error: err.message })
              : tOutside("chat.toast.modelChangeFailed");
          toast(msg);
        }
      }}
      activeKBId={conv?.knowledge_base_id ?? null}
      onKBPick={async (kbId) => {
        if (!convId) return;
        try {
          await setConversationKB(convId, kbId);
          toast(kbId ? tOutside("chat.toast.kbBound") : tOutside("chat.toast.kbUnbound"));
        } catch {
          toast(tOutside("chat.toast.kbChangeFailed"));
        }
      }}
      onSend={(t, fileIds, imageMode, videoMode) =>
        void sendMessage(convId, t, fileIds, imageMode, videoMode)
      }
      onStop={() => void cancelGeneration(convId)}
      injectedText={injectedText}
      onInjectedTextConsumed={() => setInjectedText(null)}
    />
  );

  // The temporary-chat strip travels WITH the composer so it shows in
  // both render branches below without being mounted twice, and so it
  // does not depend on the header — which is hidden on a narrow window,
  // taking the memory chip with it.
  const composerBlock = (
    <>
      {/* FIX130 C3.5 — below md the header (and its meter) is hidden, so
          the phone gets the meter as a tappable chip here. Passive: the
          header's instance, mounted but hidden, fetches the numbers. */}
      <div className="md:hidden">
        <ContextMeter conversationId={convId} variant="compact" passive />
      </div>
      <TemporaryChatBanner conversationId={convId} />
      <MessageQueue conversationId={convId} />
      {composer}
    </>
  );

  const isEmpty = messages.length === 0;

  return (
    <>
      {/* Slim desktop header — no centre label (the model name
          already lives in the composer pill below, so showing it
          here was redundant). Just the file-attachments indicator
          + the "..." menu on the right. */}
      <div className="hidden items-center justify-end gap-1 px-4 pt-1 md:flex">
        {/* FIX33 — quiet context meter: nothing until 70 % of the budget. */}
        <ContextMeter conversationId={convId} />
        <MemoryIndicator conversationId={convId} />
        <WorkspaceButton
          conversationId={convId}
          onOpen={() => openPane({ kind: "workspace" })}
        />
        <ConversationFiles conversationId={convId} />
        <ExportMenu
          conversationId={convId}
          onShare={() => setShareOpen(true)}
        />
      </div>
      {/* FIX76 — the thread column and the ONE side-pane slot sit in a
          row. At ≥ lg an open pane docks as a flex sibling (the thread
          narrows, the composer and auto-scroll keep working); below lg
          the same panels render as slide-over dialogs. The slot holds
          one pane at a time (sidePaneStore): opening another replaces
          it. */}
      <div className="flex min-h-0 flex-1">
      <AttachmentDropZone
        className="flex min-w-0 flex-1 flex-col overflow-hidden"
        enabled={!streaming}
        onFiles={(filesRaw) => {
          // W3 — same vision gate as the composer's picker/paste paths.
          const files = gateImagesForVision(filesRaw, conv?.model_id ?? null);
          if (files.length === 0) return;
          void uploadFiles(convId, files, {
            addPendingFile,
            updateFileProgress,
            markFileReady,
            markFileError,
            setFileExtractionStatus,
          });
        }}
      >
        {isEmpty ? (
          // Empty conversation → centred hero block matching the
          // /chat landing layout: greeting + composer + chips,
          // vertically centred. The instant the user sends, the
          // assistant placeholder lands in chatStore.messages,
          // messages.length flips to 2, and the layout swaps to
          // the bottom-anchored thread view below.
          <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto">
            <EmptyState onPrompt={(text) => setInjectedText(text)}>
              {composerBlock}
            </EmptyState>
          </div>
        ) : (
          <>
            <ErrorBoundary resetKey={convId}>
              <ChatThread
                convId={convId}
                // FIX127 D4 — an answer that was regenerated is a branch the
                // server keeps (the navigator shows it after a reload); live,
                // it used to stay stacked above the new answer.
                messages={visibleMessages}
                hasMoreOlder={hasMoreOlder ?? false}
                loadingOlder={loadingOlder ?? false}
                onLoadEarlier={() => loadOlderMessages(convId)}
                onRegenerate={(messageId) => void regenerate(convId, messageId)}
                onPromptPick={(text) => {
                  setInjectedText(text);
                }}
                // FIX33 B5 — quote-reply lands in the composer as a blockquote.
                onQuote={(quoted) => setInjectedText(quoted)}
                onEditMessage={(messageId, newContent) =>
                  void editMessage(convId, messageId, newContent)
                }
                onSwitchBranch={(messageId, branchIndex) =>
                  void switchBranch(convId, messageId, branchIndex)
                }
              />
            </ErrorBoundary>
            {composerBlock}
          </>
        )}
      </AttachmentDropZone>
      <WorkspacePanel
        convId={convId}
        open={pane?.kind === "workspace"}
        onClose={() => closePaneIf("workspace")}
      />
      {/* Phase 6 / C1 — Canvas pane. Store-driven (opened from
          CodeBlock's "Open in Canvas" buttons). */}
      <CanvasPanel />
      <FilePreviewPane />
      </div>
      <ShareModal
        open={shareOpen}
        conversationId={convId}
        onClose={() => setShareOpen(false)}
      />
    </>
  );
}
