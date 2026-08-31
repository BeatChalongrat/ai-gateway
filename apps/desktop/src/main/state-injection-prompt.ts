// FIX33.1 Part 2 — the state INJECTION wording exists ONCE.
//
// Canonical asset: chat-edge's prompt_state_injection.txt (Go: StateInjectionTexts).
// Both surfaces carry <conversation_state> as a user/assistant pair at the head
// of the history — never inside the system message, which the default DLP
// profile does not scan outbound. test/compaction-prompt.test.ts byte-compares
// this file to the asset. Generated from the .txt — do not hand-edit.

export const STATE_INJECTION_USER_PREAMBLE = "[Context from earlier in this conversation — reference only, not a new request]";
export const STATE_INJECTION_ASSISTANT_ACK = "Understood — I'll keep that summary as background and continue from where we left off.";
