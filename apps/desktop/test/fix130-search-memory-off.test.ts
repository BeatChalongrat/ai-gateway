// FIX130 A2 — search_past_conversations stays available in a chat whose
// memory is off (or a temporary chat): past conversations are the user's own
// history, not memory. But chat-edge's /api/v1/search also returns SAVED
// MEMORIES (type "memory"), and handing those to the model in such a chat is
// the memory read the switch turned off, arriving through a tool. chat-edge's
// own copy of the tool drops them the same way (tools.go
// execSearchPastConversations).
import { describe, expect, it } from "vitest";
import { HostTools } from "../src/main/host-tools";

function toolsReturning(results: unknown[]): { tools: HostTools; scopes: string[] } {
  const scopes: string[] = [];
  const chatEdge = {
    searchPastConversations: async (_q: string, scope: string) => {
      scopes.push(scope);
      return { query: "q", scope, total: results.length, results };
    },
  };
  const log = { info() {}, warn() {}, error() {} };
  const tools = new HostTools({} as never, chatEdge as never, (async () => ({})) as never, log as never, () => false);
  return { tools, scopes };
}

const HITS = [
  { type: "message", conversation_id: "c1", content: "เราคุยเรื่องทริปเชียงใหม่" },
  { type: "memory", content: "ชื่อเล่นว่ากะทิ" },
];

const ctx = (memoryEnabled: boolean | undefined) => ({
  conversationId: "11111111-1111-4111-8111-111111111111",
  toolCallId: "t1",
  signal: new AbortController().signal,
  memoryEnabled,
});

describe("FIX130 — search in a chat whose memory is not active", () => {
  it("drops saved-memory hits and keeps past-conversation hits", async () => {
    const { tools } = toolsReturning(HITS);
    const r = await tools.execute("search_past_conversations", { query: "เชียงใหม่" }, ctx(false));
    expect(r.ok).toBe(true);
    expect(r.text).toContain("ทริปเชียงใหม่");
    expect(r.text).not.toContain("กะทิ");
    expect(JSON.parse(r.text).total).toBe(1);
  });

  it("refuses a memories-only search", async () => {
    const { tools, scopes } = toolsReturning(HITS);
    const r = await tools.execute("search_past_conversations", { query: "ชื่อ", scope: "memories" }, ctx(false));
    expect(r.ok).toBe(false);
    expect(r.text).not.toContain("กะทิ");
    expect(scopes).toEqual([]); // never asked
  });

  it("an ordinary memory-on chat still gets every hit", async () => {
    const { tools } = toolsReturning(HITS);
    const r = await tools.execute("search_past_conversations", { query: "ชื่อ" }, ctx(true));
    expect(r.text).toContain("กะทิ");
  });
});
