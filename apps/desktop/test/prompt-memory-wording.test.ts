// FIX63 — the desktop's memory block says the same thing as the web's.
//
// Before: "Durable facts … they are hints, not guarantees" for every saved
// memory, while chat-edge's frame told the model that standing
// instructions are to be followed in every response. One saved memory,
// two contracts, depending on which client the user opened. The web
// wording is the measured one (FIX49 Part D, FIX56, FIX62); this pins the
// desktop to it: instructions under "follow them", facts under "use when
// relevant", local sidecar notes as facts, and the closing line that keeps
// the memory system out of the reply.
import { describe, expect, it } from "vitest";
import { PromptAssembler } from "../src/main/prompt-assembler";
import type { ChatEdgeClient } from "../src/main/chat-edge-client";
import type { Logger } from "../src/main/logger";

const noopLog = { info() {}, warn() {}, error() {}, debug() {} } as unknown as Logger;

function assembler(memories: { id: string; content: string; created_at: string; memory_type?: string }[]) {
  const chatEdge = { listGlobalMemories: async () => memories, listProjects: async () => [] } as unknown as ChatEdgeClient;
  return new PromptAssembler(chatEdge, noopLog);
}

const baseInputs = {
  homeDirectory: "C:\\Users\\test",
  conversationId: "c1",
  activeProject: null,
  memoryEnabled: true,
  toolsAvailable: true,
  localConversationNotes: [],
};

describe("FIX63 — memory wording matches the web frame", () => {
  it("renders standing instructions under 'follow them' and facts under 'use when relevant'", async () => {
    const a = assembler([
      { id: "i1", content: "Answer in at most five bullets.", created_at: "2026-09-01T00:00:00Z", memory_type: "response_preference" },
      { id: "i2", content: "Code examples in TypeScript, never JavaScript.", created_at: "2026-09-02T00:00:00Z", memory_type: "confirmed_instruction" },
      { id: "f1", content: "The company database is PostgreSQL.", created_at: "2026-09-03T00:00:00Z", memory_type: "explicit_fact" },
    ]);
    const prompt = await a.buildSystemPrompt({ ...baseInputs, localGlobalNotes: [] });
    const block = prompt.slice(prompt.indexOf("## Memory (global)"));

    const follow = block.indexOf("Follow them in every response");
    const facts = block.indexOf("Use them when relevant — do not force them into every response");
    expect(follow).toBeGreaterThan(-1);
    expect(facts).toBeGreaterThan(follow);
    // each memory sits under its own heading
    expect(block.indexOf("Answer in at most five bullets.")).toBeGreaterThan(follow);
    expect(block.indexOf("Answer in at most five bullets.")).toBeLessThan(facts);
    expect(block.indexOf("Code examples in TypeScript")).toBeLessThan(facts);
    expect(block.indexOf("The company database is PostgreSQL.")).toBeGreaterThan(facts);
    expect(block).toContain("Do not say 'according to my memory' or mention the memory system.");
    expect(block).not.toContain("hints, not guarantees");
  });

  it("a local sidecar note has no class and is rendered as a fact", async () => {
    const a = assembler([]);
    const prompt = await a.buildSystemPrompt({
      ...baseInputs,
      localGlobalNotes: [{ content: "the user's team is Platform", createdAt: "2026-08-25T11:00:00Z" }],
    });
    expect(prompt).not.toContain("Follow them in every response");
    expect(prompt).toContain("Use them when relevant");
    expect(prompt).toContain("- the user's team is Platform");
  });

  it("a memory without a class from an older server is a fact, never an instruction", async () => {
    const a = assembler([{ id: "x", content: "Prefers dark mode.", created_at: "2026-09-01T00:00:00Z" }]);
    const prompt = await a.buildSystemPrompt({ ...baseInputs, localGlobalNotes: [] });
    expect(prompt).not.toContain("Follow them in every response");
    expect(prompt).toContain("- Prefers dark mode.");
  });
});
