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

  it("local notes render even when memory is disabled (server memories are the only thing gated) — WPF parity", async () => {
    const a = assemblerWithServerMemories([{ id: "s1", content: "server-only fact", created_at: "2026-08-25T09:00:00Z" }]);
    const prompt = await a.buildSystemPrompt({
      ...baseInputs,
      memoryEnabled: false,
      localGlobalNotes: [{ content: "local global fact", createdAt: "2026-08-25T11:00:00Z" }],
      localConversationNotes: [{ content: "local conv fact", createdAt: "2026-08-25T11:00:00Z" }],
    });
    expect(prompt).toContain("local global fact");
    expect(prompt).toContain("local conv fact");
    expect(prompt).not.toContain("server-only fact"); // server half respects memoryEnabled
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
