"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";
import { ChatInput, uploadFiles } from "@/components/chat/ChatInput";
import { ChatThread } from "@/components/chat/ChatThread";
import { ConversationFiles } from "@/components/chat/ConversationFiles";
import { EmptyState } from "@/components/chat/EmptyState";
import { ExportMenu } from "@/components/chat/ExportMenu";
import { ShareModal } from "@/components/chat/ShareModal";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { MessageThreadSkeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { useIsStreaming } from "@/hooks/useStream";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

export default function ConversationPage() {
  const params = useParams();
  const router = useRouter();
  const convId =
    typeof params?.conversationId === "string" ? params.conversationId : null;

  const conversations = useChatStore((s) => s.conversations);
  const messages = useChatStore((s) =>
    convId ? s.messages[convId] : undefined,
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
    if (useChatStore.getState().messages[convId] === undefined) {
      void loadMessages(convId);
    }
  }, [convId, setActive, loadMessages]);

  if (!convId) {
    router.replace("/chat");
    return null;
  }
  if (messages === undefined) {
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
        try {
          await setConversationModel(convId, modelId);
          selectGlobalModel(modelId);
          const m = getModelById(modelId);
          toast(`Switched to ${m?.name ?? modelId}`);
        } catch {
          toast("Couldn't change model.");
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
        <ConversationFiles conversationId={convId} />
        <ExportMenu
          conversationId={convId}
          onShare={() => setShareOpen(true)}
        />
      </div>

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
