"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";
import { ChatInput, uploadFiles } from "@/components/chat/ChatInput";
import { ChatThread } from "@/components/chat/ChatThread";
import { ConversationFiles } from "@/components/chat/ConversationFiles";
import { EmptyState } from "@/components/chat/EmptyState";
import { ExportMenu } from "@/components/chat/ExportMenu";
import { MemoryIndicator } from "@/components/chat/MemoryIndicator";
import { ShareModal } from "@/components/chat/ShareModal";
import { WorkspaceButton } from "@/components/workspace/WorkspaceButton";
import { WorkspacePanel } from "@/components/workspace/WorkspacePanel";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { MessageThreadSkeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { useIsStreaming } from "@/hooks/useStream";
import { ApiError } from "@/lib/api";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

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
  const selectGlobalModel = useModelStore((s) => s.selectModel);
  const getModelById = useModelStore((s) => s.getModelById);

  const streaming = useIsStreaming(convId);
  const conv = conversations.find((c) => c.id === convId);

  const [injectedText, setInjectedText] = useState<string | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);

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

  if (!convId) {
    router.replace("/chat");
    return null;
  }
  // Skeleton renders ONLY when the fetch has not yet completed.
  // Once messagesLoaded[convId] flips to true the page falls through
  // to the empty-vs-thread branch below — even if the load failed,
  // because the errorBanner already surfaces the failure.
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
          toast(`Switched to ${label}`);
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
              ? `Couldn't change model: ${err.message}`
              : "Couldn't change model.";
          toast(msg);
        }
      }}
      activeKBId={conv?.knowledge_base_id ?? null}
      onKBPick={async (kbId) => {
        if (!convId) return;
        try {
          await setConversationKB(convId, kbId);
          toast(kbId ? "Knowledge base bound" : "Knowledge base unbound");
        } catch {
          toast("Couldn't change knowledge base.");
        }
      }}
      onSend={(t, fileIds) => void sendMessage(convId, t, fileIds)}
      onStop={() => void cancelGeneration(convId)}
      injectedText={injectedText}
      onInjectedTextConsumed={() => setInjectedText(null)}
    />
  );

  const isEmpty = messages.length === 0;

  return (
    <>
      {/* Slim desktop header — no centre label (the model name
          already lives in the composer pill below, so showing it
          here was redundant). Just the file-attachments indicator
          + the "..." menu on the right. */}
      <div className="hidden items-center justify-end gap-1 px-4 pt-1 md:flex">
        <MemoryIndicator conversationId={convId} />
        <WorkspaceButton
          conversationId={convId}
          onOpen={() => setWorkspaceOpen(true)}
        />
        <ConversationFiles conversationId={convId} />
        <ExportMenu
          conversationId={convId}
          onShare={() => setShareOpen(true)}
        />
      </div>
      <WorkspacePanel
        convId={convId}
        open={workspaceOpen}
        onClose={() => setWorkspaceOpen(false)}
      />

      <AttachmentDropZone
        className="flex flex-1 flex-col overflow-hidden"
        enabled={!streaming}
        onFiles={(files) => {
          void uploadFiles(convId, files, {
            addPendingFile,
            updateFileProgress,
            markFileReady,
            markFileError,
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
              {composer}
            </EmptyState>
          </div>
        ) : (
          <>
            <ErrorBoundary resetKey={convId}>
              <ChatThread
                convId={convId}
                modelId={conv?.model_id ?? null}
                messages={messages}
                hasMoreOlder={hasMoreOlder ?? false}
                loadingOlder={loadingOlder ?? false}
                onLoadEarlier={() => loadOlderMessages(convId)}
                onRegenerate={(messageId) => void regenerate(convId, messageId)}
                onPromptPick={(text) => {
                  setInjectedText(text);
                }}
                onEditMessage={(messageId, newContent) =>
                  void editMessage(convId, messageId, newContent)
                }
                onSwitchBranch={(messageId, branchIndex) =>
                  void switchBranch(convId, messageId, branchIndex)
                }
              />
            </ErrorBoundary>
            {composer}
          </>
        )}
      </AttachmentDropZone>
      <ShareModal
        open={shareOpen}
        conversationId={convId}
        onClose={() => setShareOpen(false)}
      />
    </>
  );
}
