"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ChatInput } from "@/components/chat/ChatInput";
import { ChatThread } from "@/components/chat/ChatThread";
import { Spinner } from "@/components/ui/Spinner";
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
  const sendMessage = useChatStore((s) => s.sendMessage);
  const cancelGeneration = useChatStore((s) => s.cancelGeneration);
  const regenerate = useChatStore((s) => s.regenerate);
  const setConversationModel = useChatStore((s) => s.setConversationModel);
  const selectGlobalModel = useModelStore((s) => s.selectModel);
  const getModelById = useModelStore((s) => s.getModelById);

  const streaming = useIsStreaming(convId);
  const conv = conversations.find((c) => c.id === convId);

  // Allow the empty-state prompt pills to either auto-send (default)
  // or populate the input. Spec says auto-send; we honour that, but
  // route through ChatInput's "injected text" hatch so the user
  // briefly sees the prompt text before send.
  const [injectedText, setInjectedText] = useState<string | null>(null);

  useEffect(() => {
    if (convId) {
      setActive(convId);
      if (messages === undefined) void loadMessages(convId);
    }
  }, [convId, setActive, messages, loadMessages]);

  if (!convId) {
    router.replace("/chat");
    return null;
  }
  if (messages === undefined) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner size={24} />
      </div>
    );
  }

  return (
    <>
      <div className="hidden border-b border-border bg-bg-base/60 px-10 py-3 md:block">
        <div className="mx-auto flex max-w-3xl items-center justify-between">
          <h2 className="truncate text-sm font-semibold tracking-tight">
            {conv?.title ?? "Conversation"}
          </h2>
          {conv?.model_id ? (
            <span className="text-[11px] uppercase tracking-wider text-text-muted">
              {getModelById(conv.model_id)?.name ?? conv.model_id}
            </span>
          ) : null}
        </div>
      </div>

      <ChatThread
        convId={convId}
        modelId={conv?.model_id ?? null}
        messages={messages}
        onRegenerate={(messageId) => void regenerate(convId, messageId)}
        onPromptPick={(text) => {
          // Auto-send immediately.
          void sendMessage(convId, text);
        }}
      />

      <ChatInput
        disabled={false}
        streaming={streaming}
        activeModelId={conv?.model_id ?? null}
        onModelPick={async (modelId) => {
          // Conversation pages: the picked model becomes the conv's
          // new binding AND the user's global default + recents
          // entry. PATCH first; toast on success; bail on failure.
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
        onSend={(t) => void sendMessage(convId, t)}
        onStop={() => void cancelGeneration(convId)}
        injectedText={injectedText}
        onInjectedTextConsumed={() => setInjectedText(null)}
      />
    </>
  );
}
