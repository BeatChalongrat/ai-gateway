"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AttachmentDropZone } from "@/components/chat/AttachmentDropZone";
import { ChatInput, uploadFiles } from "@/components/chat/ChatInput";
import { ChatThread } from "@/components/chat/ChatThread";
import { ConversationFiles } from "@/components/chat/ConversationFiles";
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
  // 150ms minimum skeleton hold so fast loads don't strobe.
  const [skeletonHeld, setSkeletonHeld] = useState(false);

  useEffect(() => {
    if (convId) {
      setActive(convId);
      if (messages === undefined) {
        setSkeletonHeld(true);
        const t = window.setTimeout(() => setSkeletonHeld(false), 150);
        void loadMessages(convId);
        return () => window.clearTimeout(t);
      }
    }
  }, [convId, setActive, messages, loadMessages]);

  if (!convId) {
    router.replace("/chat");
    return null;
  }
  if (messages === undefined || skeletonHeld) {
    return (
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="mx-auto w-full max-w-3xl flex-1 overflow-y-auto px-4 py-6 md:px-10">
          <MessageThreadSkeleton />
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Slim desktop header — model name in the centre, "..."
          menu on the right, no conversation title (the sidebar is
          the source of truth for which conversation is open). */}
      <div className="hidden items-center justify-between px-4 pt-1 md:flex">
        <div className="w-8" aria-hidden />
        <span className="text-[13px] font-medium text-text-secondary">
          {conv?.model_id
            ? (getModelById(conv.model_id)?.name ?? conv.model_id)
            : "SRAN Chat"}
        </span>
        <div className="flex items-center gap-1">
          <ConversationFiles conversationId={convId} />
          <ExportMenu
            conversationId={convId}
            onShare={() => setShareOpen(true)}
          />
        </div>
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
              // Populate the textarea so the user can review /
              // extend the chip-supplied prompt before sending.
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
      </AttachmentDropZone>
      <ShareModal
        open={shareOpen}
        conversationId={convId}
        onClose={() => setShareOpen(false)}
      />
    </>
  );
}
