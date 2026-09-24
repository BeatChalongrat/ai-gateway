// FIX132 F17 — what a conversation id looks like. chat-edge ids are UUIDs;
// "/chat/undefined" (a link built from a missing id) or a typo is not one,
// and the page must not treat it as a new, empty chat you can send into.
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isConversationId(id: string | null | undefined): id is string {
  return typeof id === "string" && UUID_RE.test(id);
}
