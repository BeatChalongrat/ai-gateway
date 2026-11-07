// FIX28 §5.1 — the local chain history IS what the desktop sends to the
// provider on the next turn (buildMessages loads it verbatim). Until FIX28 it
// held only user turns and tool chains: runLoop never appended the assistant's
// final answer, so every multi-turn desktop conversation reached the model as
// a run of user messages with its own replies missing.
import type { GatewayMessage } from "./gateway-client";

/** Append the turn's final assistant text to the chain (no-op when empty). */
export function appendAssistantReply(messages: GatewayMessage[], assistantText: string): void {
  const text = assistantText.trim();
  if (!text) return;
  const last = messages[messages.length - 1];
  // runLoop already pushed an assistant message carrying tool_calls; the
  // final answer is a separate, plain assistant message after the tool results.
  if (last && last.role === "assistant" && !last.tool_calls && last.content === text) return;
  messages.push({ role: "assistant", content: text });
}

/** The role sequence of a chain, for logs and tests. */
export function roleSequence(messages: readonly GatewayMessage[]): string[] {
  return messages.map((m) => m.role);
}
