// FIX130 A2 — what a conversation does with memory, in one rule.
//
//   on        — reads the saved memories and may save / suggest new ones
//   off       — memory switched off on an ordinary chat: reads nothing,
//               writes nothing; the user can switch it back on
//   temporary — started as a temporary chat (is_temporary): reads nothing,
//               writes nothing, and memory cannot be switched on (chat-edge
//               answers 409); the transcript is still kept
//
// chat-edge's Conversation.MemoryActive() is the server half of the same
// rule (memory_enabled && !is_temporary). A temporary chat stays temporary
// even if its memory_enabled flag says on — a row switched on before the
// server refused that. A payload without memory_enabled means ON: failing
// the other way would claim a privacy the chat does not have.

import type { Conversation } from "./types";

export type MemoryMode = "on" | "off" | "temporary";

export function memoryModeOf(
  conv: Pick<Conversation, "memory_enabled" | "is_temporary"> | null | undefined,
): MemoryMode {
  if (conv?.is_temporary === true) return "temporary";
  if (conv?.memory_enabled === false) return "off";
  return "on";
}
