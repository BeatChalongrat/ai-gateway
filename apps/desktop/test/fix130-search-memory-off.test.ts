// FIX130 A2 — a chat whose memory is not active (switched off, or a temporary
// chat) reads NO memory, and recalling past chats is memory: chat-edge's
// /api/v1/search returns other chats and saved memories alike. So the tool is
// not offered there (turn-coordinator, withholdMemoryTools) and a call that
// arrives anyway is refused without asking chat-edge — the same rule as
// chat-edge's own copy (tools.go ToolDefs / execSearchPastConversations).
// FIX56.1 had kept it as "read-only"; the card reverses that: a temporary
// chat neither reads nor writes.
import { describe, expect, it } from "vitest";
import { HostTools } from "../src/main/host-tools";
import { withholdMemoryTools } from "../src/shared/bridge-protocol";

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

const def = (name: string) => ({ type: "function" as const, function: { name, description: "", parameters: {} } });

describe("FIX130 — past-chat search in a chat whose memory is not active", () => {
  it("is refused and never asks chat-edge", async () => {
    for (const scope of ["all", "messages", "memories"]) {
      const { tools, scopes } = toolsReturning(HITS);
      const r = await tools.execute("search_past_conversations", { query: "เชียงใหม่", scope }, ctx(false));
      expect(r.ok).toBe(false);
      expect(r.text).toMatch(/^REFUSED/);
      expect(r.text).not.toContain("ทริปเชียงใหม่");
      expect(r.text).not.toContain("กะทิ");
      expect(scopes).toEqual([]);
    }
  });

  it("an ordinary memory-on chat still gets every hit", async () => {
    const { tools } = toolsReturning(HITS);
    const r = await tools.execute("search_past_conversations", { query: "ชื่อ" }, ctx(true));
    expect(r.ok).toBe(true);
    expect(r.text).toContain("ทริปเชียงใหม่");
    expect(r.text).toContain("กะทิ");
  });

  it("is not offered — nor is remember — when memory is not active; both are when it is", () => {
    const defs = ["web_search", "remember", "search_past_conversations", "run_python"].map(def);
    expect(withholdMemoryTools(defs, false).map((d) => d.function.name)).toEqual(["web_search", "run_python"]);
    expect(withholdMemoryTools(defs, true).map((d) => d.function.name)).toEqual(["web_search", "remember", "search_past_conversations", "run_python"]);
  });
});
