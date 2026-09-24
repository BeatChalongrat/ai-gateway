// FIX132 F12 — "open this chat AT that message".
//
// A search hit (the sidebar / Ctrl+K), a #msg-<id> deep link, and the
// thread's "go to the first message" button all ask through here; the
// conversation page answers: it loads the gap down to the message
// (chatStore.loadThrough — one `through=` request instead of 50 rows per
// "load older"), then the thread scrolls to it and highlights it briefly.
// A store rather than the URL hash alone: pushing the same path with a new
// hash fires no hashchange, and a hit inside the chat already open must
// still jump.

import { create } from "zustand";

/** A message id, or "first" for the conversation's first message. */
export type JumpTarget = string;

export interface JumpRequest {
  convId: string;
  target: JumpTarget;
  nonce: number;
}

interface MessageJumpState {
  request: JumpRequest | null;
}

export const useMessageJumpStore = create<MessageJumpState>()(() => ({ request: null }));

let nonce = 0;

export function requestMessageJump(convId: string, target: JumpTarget): void {
  nonce += 1;
  useMessageJumpStore.setState({ request: { convId, target, nonce } });
}

export function clearMessageJump(n: number): void {
  if (useMessageJumpStore.getState().request?.nonce === n) useMessageJumpStore.setState({ request: null });
}

/** "#msg-<id>" → "<id>"; anything else → null. */
export function jumpTargetFromHash(hash: string): string | null {
  if (!hash.startsWith("#msg-")) return null;
  const id = hash.slice(5);
  return id ? id : null;
}
