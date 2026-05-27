"use client";

// /chat landing page — ChatGPT / Claude style.
//
// Renders the personalised greeting + category chips + the same
// rounded-pill input you get inside a conversation. The conversation
// is created lazily — only when the user actually sends their first
// message. Click "+ New chat" and walk away → nothing in the sidebar.
//
// Note: we deliberately do NOT auto-redirect to the most recent
// existing conversation. The redesigned UX wants a clean landing
// surface (matches ChatGPT's "Where should we begin?" + Claude's
// "Beat returns!"), not "open whatever you were working on last".

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ChatInput } from "@/components/chat/ChatInput";
import { EmptyState } from "@/components/chat/EmptyState";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

export default function ChatLanding() {
  const router = useRouter();
  const createConversation = useChatStore((s) => s.createConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const selectedModelId = useModelStore((s) => s.selectedModelId);
  const selectGlobalModel = useModelStore((s) => s.selectModel);
  // Landing text is buffered locally so the user sees what they
  // typed survive the navigation into the new conversation.
  const [injectedText, setInjectedText] = useState<string | null>(null);

  async function handleSend(text: string) {
    if (!selectedModelId) return;
    // Lazy create: the conversation row is born here, at first send,
    // not when the user clicked "+ New chat". This is the bit that
    // keeps the sidebar from collecting empty "New conversation"
    // stubs the user never followed through on.
    const fresh = await createConversation(selectedModelId);
    // Kick off the stream BEFORE navigating so the assistant
    // placeholder mounts inside chatStore by the time the
    // conversation page renders. The page sees messages already
    // streaming via the store; no extra hand-off needed.
    void sendMessage(fresh.id, text);
    router.push(`/chat/${fresh.id}`);
  }

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto">
      <EmptyState onPrompt={(text) => setInjectedText(text)}>
        <ChatInput
          conversationId={null}
          disabled={false}
          streaming={false}
          activeModelId={selectedModelId ?? null}
          onModelPick={(modelId) => selectGlobalModel(modelId)}
          onSend={(t) => void handleSend(t)}
          onStop={() => undefined}
          injectedText={injectedText}
          onInjectedTextConsumed={() => setInjectedText(null)}
        />
      </EmptyState>
    </div>
  );
}
