"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

export default function ChatLanding() {
  const router = useRouter();
  const conversations = useChatStore((s) => s.conversations);
  const loaded = useChatStore((s) => s.conversationsLoaded);
  const createConversation = useChatStore((s) => s.createConversation);
  const selectedModelId = useModelStore((s) => s.selectedModelId);
  const modelsLoaded = useModelStore((s) => s.loaded);

  useEffect(() => {
    if (!loaded || !modelsLoaded) return;
    if (conversations.length > 0) {
      router.replace(`/chat/${conversations[0].id}`);
    }
  }, [loaded, modelsLoaded, conversations, router]);

  async function handleStartFirst() {
    if (!selectedModelId) return;
    const c = await createConversation(selectedModelId);
    router.replace(`/chat/${c.id}`);
  }

  return (
    <div className="flex flex-1 items-center justify-center px-4">
      <div className="fade-in-up max-w-md text-center">
        <div className="mx-auto mb-4 h-14 w-14 rounded-2xl bg-gradient-to-br from-accent to-accent-subtle shadow-2xl" />
        <h1 className="text-2xl font-semibold tracking-tight">
          Welcome to SRAN Chat
        </h1>
        <p className="mt-2 text-sm text-text-secondary">
          A private, secure AI chat surface. Start a new conversation to
          ask anything.
        </p>
        <button
          type="button"
          onClick={handleStartFirst}
          disabled={!selectedModelId}
          className="mt-5 rounded-lg bg-accent px-5 py-2.5 text-sm font-medium text-white shadow-md transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          Start a new chat
        </button>
      </div>
    </div>
  );
}
