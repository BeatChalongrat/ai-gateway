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

// FIX56.1 Part 1 — the owner's decision, and the second reversal of the
// same assertion in two rounds. Worth stating plainly rather than quietly
// rewriting: FIX56 made a memory-off chat read NOTHING, because the switch
// then meant two different things in the two clients and the desktop was
// the one quietly still reading. That asymmetry is what had to die, and it
// stays dead — both stores are still gated identically here. What moved is
// where the shared line sits: "don't record me" no longer also means
// "forget who I am".
//
// The half that is NOT a preference and must never move: nothing is
// written. That is enforced elsewhere (memory tools withheld from the
// request, `remember` refused at dispatch, sub-agents inheriting the flag)
// and none of it reads tempChatReadsMemory.
describe("FIX56.1 — a temporary chat reads memory, writes none, and says so", () => {
  const bothStores = {
    localGlobalNotes: [{ content: "local global fact", createdAt: "2026-08-25T11:00:00Z" }],
    localConversationNotes: [{ content: "local conv fact", createdAt: "2026-08-25T11:00:00Z" }],
  };
  const serverMem = [{ id: "s1", content: "server-only fact", created_at: "2026-08-25T09:00:00Z" }];

  it("reads BOTH stores in a memory-off chat under the default setting", async () => {
    const a = assemblerWithServerMemories(serverMem);
    const prompt = await a.buildSystemPrompt({ ...baseInputs, memoryEnabled: false, ...bothStores });
    expect(prompt).toContain("local global fact");
    expect(prompt).toContain("local conv fact");
    expect(prompt).toContain("server-only fact");
  });

  it("reads NEITHER store when the deployment sets tempChatReadsMemory=false", async () => {
    const chatEdge = {
      listGlobalMemories: async () => serverMem,
      listProjects: async () => [],
    } as unknown as ChatEdgeClient;
    const a = new PromptAssembler(chatEdge, noopLog, { tempChatReadsMemory: false });
    const prompt = await a.buildSystemPrompt({ ...baseInputs, memoryEnabled: false, ...bothStores });
    expect(prompt).not.toContain("local global fact");
    expect(prompt).not.toContain("local conv fact");
    expect(prompt).not.toContain("server-only fact");
    expect(prompt).not.toContain("## Memory (global)");
    // Still told it cannot remember — the stricter setting must not make
    // the model MORE likely to promise a save.
    expect(prompt).toContain("## Temporary chat");
  });

  it("keeps the two stores in step under BOTH settings — neither may be read alone", async () => {
    // The FIX56 bug in one assertion: the desktop read the local store in a
    // chat where the server store was gated off. Whatever the setting, the
    // two are visible together or not at all.
    for (const reads of [true, false]) {
      const chatEdge = {
        listGlobalMemories: async () => serverMem,
        listProjects: async () => [],
      } as unknown as ChatEdgeClient;
      const a = new PromptAssembler(chatEdge, noopLog, { tempChatReadsMemory: reads });
      const prompt = await a.buildSystemPrompt({ ...baseInputs, memoryEnabled: false, ...bothStores });
      const sawLocal = prompt.includes("local global fact");
      const sawServer = prompt.includes("server-only fact");
      expect(sawLocal, `tempChatReadsMemory=${reads}: local and server must agree`).toBe(sawServer);
    }
  });

  it("carries the temporary-chat notice even when there is nothing to recall", async () => {
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
    expect(prompt).toContain("## Temporary chat");
    expect(prompt).toMatch(/no tool that can save anything/i);
    // And it names what IS kept, so "temporary" cannot be read as
    // "this conversation disappears".
    expect(prompt).toMatch(/still saved to the user's chat history/i);
  });

  it("adds no such notice when memory is on", async () => {
    const a = assemblerWithServerMemories([]);
    const prompt = await a.buildSystemPrompt({ ...baseInputs, memoryEnabled: true });
    expect(prompt).not.toContain("## Temporary chat");
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
