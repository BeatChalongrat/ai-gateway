"use client";

import { useChatStore } from "@/stores/chatStore";

/** Returns true when the named conversation has an in-flight stream. */
export function useIsStreaming(convId: string | null): boolean {
  return useChatStore((s) =>
    convId
      ? Boolean(s.abortControllers[convId] || s.streamingAssistantId[convId])
      : false,
  );
}
