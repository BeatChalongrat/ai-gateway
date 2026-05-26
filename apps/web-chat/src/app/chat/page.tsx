"use client";

// /chat landing page — ChatGPT / Claude style.
//
// Renders the personalised greeting + category chips + the same
// rounded-pill input you get inside a conversation. Typing here
// reuses a recent empty conversation if one exists (the same
// dedup heuristic the sidebar's "+ New chat" button uses);
// otherwise it spawns a fresh conversation, navigates to it, and
// immediately streams the user's first message into it.
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
  const conversations = useChatStore((s) => s.conversations);
  const createConversation = useChatStore((s) => s.createConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const selectedModelId = useModelStore((s) => s.selectedModelId);
  const selectGlobalModel = useModelStore((s) => s.selectModel);
  // Landing text is buffered locally so the user sees what they
  // typed survive the navigation into the new conversation.
  const [injectedText, setInjectedText] = useState<string | null>(null);

  // Reuse any empty conversation regardless of model / age. Same
  // rule as useNewChatHandler — see that file for the rationale.
  function findReusableConversation(): string | null {
    const candidate = conversations.find((c) => c.message_count === 0);
    return candidate ? candidate.id : null;
  }

  async function handleSend(text: string) {
    if (!selectedModelId) return;
    let convId = findReusableConversation();
    if (!convId) {
      const fresh = await createConversation(selectedModelId);
      convId = fresh.id;
    }
    // Kick off the stream BEFORE navigating so the assistant
    // placeholder mounts inside chatStore by the time the
    // conversation page renders. The page sees messages already
    // streaming via the store; no extra hand-off needed.
    void sendMessage(convId, text);
    router.push(`/chat/${convId}`);
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
