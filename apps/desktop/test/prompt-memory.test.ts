// FIX5 §1 + §3 — the durable-memory prologue and the web-search honesty note.
//
// §1: before FIX5 the Electron prompt rendered ONLY "## Memory (global)" and fed
// it ONLY from chat-edge's server memories, so the `remember` tool (which writes
// the sidecar's LOCAL store) saved but was never recalled. These pins assert the
// WPF parity the fix restores: BOTH sections render, the local global notes merge
// with the server memories (local wins a duplicate), and the conversation notes
// appear on their own. This guards the wiring against silently regressing back to
// "save but never recall".
//
// §3: when web_search is withheld because the TENANT has it off (not the user),
// the prompt must carry the true reason so the model does not imply it merely
// lacks the tool.
import { describe, expect, it } from "vitest";

import { PromptAssembler } from "../src/main/prompt-assembler";
import type { ChatEdgeClient, MemoryDto } from "../src/main/chat-edge-client";
import type { Logger } from "../src/main/logger";

const noopLog: Logger = { info: () => {}, warn: () => {}, error: () => {} };

/** A ChatEdgeClient stub that returns a fixed set of server (global_user) memories. */
function assemblerWithServerMemories(memories: MemoryDto[]): PromptAssembler {
  const chatEdge = {
    listGlobalMemories: async () => memories,
    listProjects: async () => [],
  } as unknown as ChatEdgeClient;
  return new PromptAssembler(chatEdge, noopLog);
}

const baseInputs = {
  homeDirectory: "C:/Users/tester",
  conversationId: "conv-1",
  activeProject: null,
  memoryEnabled: true,
  toolsAvailable: true,
};

describe("FIX5 §1 — the memory prologue reads BOTH stores and renders both sections", () => {
  it("renders ## Notes (this conversation) from the local conversation notes", async () => {
    const a = assemblerWithServerMemories([]);
    const prompt = await a.buildSystemPrompt({
      ...baseInputs,
      localConversationNotes: [{ content: "the build command is npm run build", createdAt: "2026-08-25T10:00:00Z" }],
      localGlobalNotes: [],
    });
    expect(prompt).toContain("## Notes (this conversation)");
    expect(prompt).toContain("the build command is npm run build");
  });

  it("merges local global notes with server memories under ONE ## Memory (global)", async () => {
    const a = assemblerWithServerMemories([{ id: "s1", content: "the user prefers short replies", created_at: "2026-08-25T09:00:00Z" }]);
    const prompt = await a.buildSystemPrompt({
      ...baseInputs,
      localGlobalNotes: [{ content: "the user's team is Platform", createdAt: "2026-08-25T11:00:00Z" }],
      localConversationNotes: [],
    });
    // Exactly one global section, holding BOTH a local note and a server memory.
    expect(prompt.match(/## Memory \(global\)/g)?.length).toBe(1);
    expect(prompt).toContain("the user's team is Platform"); // local
    expect(prompt).toContain("the user prefers short replies"); // server
  });

  it("dedups a global note present in BOTH stores (local wins, appears once)", async () => {
    const dup = "the user's name is Nok";
    const a = assemblerWithServerMemories([{ id: "s1", content: dup, created_at: "2026-08-25T09:00:00Z" }]);
    const prompt = await a.buildSystemPrompt({
      ...baseInputs,
      localGlobalNotes: [{ content: dup, createdAt: "2026-08-25T11:00:00Z" }],
      localConversationNotes: [],
    });
    expect(prompt.split(dup).length - 1).toBe(1); // exactly one occurrence
  });

  it("with neither store populated, emits no memory sections at all", async () => {
    const a = assemblerWithServerMemories([]);
    const prompt = await a.buildSystemPrompt({ ...baseInputs, localGlobalNotes: [], localConversationNotes: [] });
    expect(prompt).not.toContain("## Memory (global)");
    expect(prompt).not.toContain("## Notes (this conversation)");
  });

  it("still renders both stores when memory is on — the gate is the switch, not a silent drop", async () => {
    const a = assemblerWithServerMemories([{ id: "s1", content: "server-only fact", created_at: "2026-08-25T09:00:00Z" }]);
    const prompt = await a.buildSystemPrompt({
      ...baseInputs,
      memoryEnabled: true,
      localGlobalNotes: [{ content: "local global fact", createdAt: "2026-08-25T11:00:00Z" }],
      localConversationNotes: [{ content: "local conv fact", createdAt: "2026-08-25T11:00:00Z" }],
    });
    expect(prompt).toContain("local global fact");
    expect(prompt).toContain("local conv fact");
    expect(prompt).toContain("server-only fact");
  });
});

// FIX56.1 made a memory-off chat READ both stores and write neither.
// FIX130 A2 reverses the read half, on the evidence: NIGHT2 found answers
// quoting a saved memory in a chat the user had switched memory off for.
// "Memory off" means nothing read and nothing written; a temporary chat is
// sealed whatever its switch says (fix130-memory-off.test.ts covers the
// temporary half and the two notices). What stays from FIX56 is the rule
// that the two stores move TOGETHER — neither may be read alone.
describe("FIX130 — a memory-off chat reads neither store, writes none, and says so", () => {
  const bothStores = {
    localGlobalNotes: [{ content: "local global fact", createdAt: "2026-08-25T11:00:00Z" }],
    localConversationNotes: [{ content: "local conv fact", createdAt: "2026-08-25T11:00:00Z" }],
  };
  const serverMem = [{ id: "s1", content: "server-only fact", created_at: "2026-08-25T09:00:00Z" }];

  it("reads NEITHER store in a memory-off chat", async () => {
    const a = assemblerWithServerMemories(serverMem);
    const prompt = await a.buildSystemPrompt({ ...baseInputs, memoryEnabled: false, ...bothStores });
    expect(prompt).not.toContain("local global fact");
    expect(prompt).not.toContain("local conv fact");
    expect(prompt).not.toContain("server-only fact");
    expect(prompt).not.toContain("## Memory (global)");
    // Still told it cannot remember.
    expect(prompt).toContain("## Memory off");
  });

  it("keeps the two stores in step — on, off and temporary, neither may be read alone", async () => {
    for (const flags of [{ memoryEnabled: true }, { memoryEnabled: false }, { memoryEnabled: true, isTemporary: true }]) {
      const a = assemblerWithServerMemories(serverMem);
      const prompt = await a.buildSystemPrompt({ ...baseInputs, ...flags, ...bothStores });
      const sawLocal = prompt.includes("local global fact");
      const sawServer = prompt.includes("server-only fact");
      expect(sawLocal, `${JSON.stringify(flags)}: local and server must agree`).toBe(sawServer);
    }
  });

  it("carries the memory-off notice even when there is nothing to recall", async () => {
    // "No memory tool attached" is not the same as "the model knows it
    // cannot remember". Without being told, it answers "got it, I'll
    // remember that" in a chat where nothing can be saved.
    const a = assemblerWithServerMemories([]);
    const prompt = await a.buildSystemPrompt({
      ...baseInputs,
      memoryEnabled: false,
      localGlobalNotes: [],
      localConversationNotes: [],
    });
    expect(prompt).toContain("## Memory off");
    expect(prompt).toMatch(/no tool that can save anything/i);
    // And it names what IS kept, so "memory off" cannot be read as
    // "this conversation disappears".
    expect(prompt).toMatch(/still saved to the user's chat history/i);
  });

  it("adds no such notice when memory is on", async () => {
    const a = assemblerWithServerMemories([]);
    const prompt = await a.buildSystemPrompt({ ...baseInputs, memoryEnabled: true });
    expect(prompt).not.toContain("## Temporary chat");
    expect(prompt).not.toContain("## Memory off");
  });
});

describe("FIX5 §3 — the web-search honesty note", () => {
  it("adds the org-disabled note when web_search is withheld for the tenant", async () => {
    const a = assemblerWithServerMemories([]);
    const prompt = await a.buildSystemPrompt({ ...baseInputs, webSearchUnavailableReason: "tenant-disabled" });
    expect(prompt).toContain("## Web search is unavailable");
    expect(prompt).toMatch(/organisation has NOT enabled it/i);
  });

  it("adds no such note when the reason is unset", async () => {
    const a = assemblerWithServerMemories([]);
    const prompt = await a.buildSystemPrompt({ ...baseInputs });
    expect(prompt).not.toContain("## Web search is unavailable");
  });
});
