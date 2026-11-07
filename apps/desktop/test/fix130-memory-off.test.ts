// FIX130 A2b / A3d / A4b — "memory off" means off on the desktop too, a
// temporary chat neither reads nor writes, the prompt says which of the two it
// is, the host knows which memories it injected, and the server-memory cache
// can be dropped.
//
// Before FIX130 (FIX56.1) a memory-off chat READ both stores — the server
// memories and the local sidecar notes — "tracelessly", and the temporary flag
// was not read at all. NIGHT2 found the web answer quoting a saved memory in a
// chat the user had switched memory off for; the desktop did the same.
import { describe, expect, it } from "vitest";

import {
  MEMORY_OFF_LEAD,
  memoryActive,
  PromptAssembler,
  TEMPORARY_CHAT_LEAD,
} from "../src/main/prompt-assembler";
import type { ChatEdgeClient, MemoryDto } from "../src/main/chat-edge-client";
import type { Logger } from "../src/main/logger";

const noopLog: Logger = { info: () => {}, warn: () => {}, error: () => {} } as unknown as Logger;

function assembler(memories: MemoryDto[] | (() => MemoryDto[])) {
  let reads = 0;
  const chatEdge = {
    listGlobalMemories: async () => {
      reads++;
      return typeof memories === "function" ? memories() : memories;
    },
    listProjects: async () => [],
  } as unknown as ChatEdgeClient;
  const a = new PromptAssembler(chatEdge, noopLog);
  return { a, reads: () => reads };
}

const base = {
  homeDirectory: "C:/Users/tester",
  conversationId: "conv-1",
  activeProject: null,
  memoryEnabled: true,
  toolsAvailable: true,
};
const bothStores = {
  localGlobalNotes: [{ content: "local global fact", createdAt: "2026-08-25T11:00:00Z" }],
  localConversationNotes: [{ content: "local conv fact", createdAt: "2026-08-25T11:00:00Z" }],
};
const serverMem: MemoryDto[] = [{ id: "s1", content: "server-only fact", created_at: "2026-08-25T09:00:00Z" }];

describe("FIX130 A2b — memory off reads nothing", () => {
  it("a memory-off chat reads NEITHER store and never asks the server", async () => {
    const { a, reads } = assembler(serverMem);
    const prompt = await a.buildSystemPrompt({ ...base, memoryEnabled: false, ...bothStores });
    expect(prompt).not.toContain("server-only fact");
    expect(prompt).not.toContain("local global fact");
    expect(prompt).not.toContain("local conv fact");
    expect(prompt).not.toContain("## Memory (global)");
    expect(reads()).toBe(0);
  });

  it("a temporary chat reads NEITHER store even with its memory switch on", async () => {
    const { a, reads } = assembler(serverMem);
    const prompt = await a.buildSystemPrompt({ ...base, memoryEnabled: true, isTemporary: true, ...bothStores });
    expect(prompt).not.toContain("server-only fact");
    expect(prompt).not.toContain("local global fact");
    expect(prompt).not.toContain("local conv fact");
    expect(reads()).toBe(0);
  });

  it("a memory-off chat is told memory is off and can be turned back on", async () => {
    const { a } = assembler([]);
    const prompt = await a.buildSystemPrompt({ ...base, memoryEnabled: false });
    expect(prompt).toContain("## Memory off");
    expect(prompt).toContain(MEMORY_OFF_LEAD);
    expect(prompt).not.toContain("## Temporary chat");
    expect(prompt).toMatch(/no tool that can save anything/i);
    expect(prompt).toMatch(/saved memories are not given to you/i);
    expect(prompt).toMatch(/still saved to the user's chat history/i);
  });

  it("a temporary chat is told it is temporary and memory cannot be turned on", async () => {
    const { a } = assembler([]);
    const prompt = await a.buildSystemPrompt({ ...base, memoryEnabled: false, isTemporary: true });
    expect(prompt).toContain("## Temporary chat");
    expect(prompt).toContain(TEMPORARY_CHAT_LEAD);
    expect(prompt).not.toContain("## Memory off");
  });

  it("a memory-on chat carries neither notice and reads both stores", async () => {
    const { a } = assembler(serverMem);
    const prompt = await a.buildSystemPrompt({ ...base, ...bothStores });
    expect(prompt).not.toContain("## Temporary chat");
    expect(prompt).not.toContain("## Memory off");
    expect(prompt).toContain("server-only fact");
    expect(prompt).toContain("local global fact");
  });

  it("the FIX56.1 read setting is gone — there is nothing to switch reads back on with", () => {
    const { a } = assembler([]);
    expect("temporaryChatReadsMemory" in a).toBe(false);
  });

  it("memoryActive: on AND not temporary; an absent field means on", () => {
    expect(memoryActive({ memory_enabled: true, is_temporary: false })).toBe(true);
    expect(memoryActive({})).toBe(true);
    expect(memoryActive(null)).toBe(true);
    expect(memoryActive({ memory_enabled: false })).toBe(false);
    expect(memoryActive({ memory_enabled: true, is_temporary: true })).toBe(false);
    expect(memoryActive({ memory_enabled: null, is_temporary: null })).toBe(true);
  });
});

describe("FIX130 A3d — the assembler reports which memories it injected", () => {
  it("lists the server memories (by id) and the local notes it rendered, labels trimmed", async () => {
    const long = "ก".repeat(100);
    const { a } = assembler([
      { id: "s1", content: "server-only fact", created_at: "2026-08-25T09:00:00Z" },
      { id: "s2", content: long, created_at: "2026-08-25T10:00:00Z" },
    ]);
    const r = await a.buildSystemPromptParts({ ...base, ...bothStores });
    const ids = r.memoryUsed.map((m) => m.id).sort();
    expect(ids).toEqual(["local:0", "s1", "s2"]);
    const trimmed = r.memoryUsed.find((m) => m.id === "s2")!;
    expect(Array.from(trimmed.label).length).toBe(81); // 80 + the ellipsis
    expect(trimmed.label.endsWith("…")).toBe(true);
    expect(r.memoryUsed.find((m) => m.id === "s1")!.label).toBe("server-only fact");
  });

  it("reports nothing when nothing was injected (memory off, or no memories)", async () => {
    const off = await assembler(serverMem).a.buildSystemPromptParts({ ...base, memoryEnabled: false, ...bothStores });
    expect(off.memoryUsed).toEqual([]);
    const empty = await assembler([]).a.buildSystemPromptParts({ ...base });
    expect(empty.memoryUsed).toEqual([]);
  });
});

describe("FIX130 A4b — the server-memory cache can be dropped", () => {
  it("an edit made on the Memory page reaches the next turn once the cache is invalidated", async () => {
    let current: MemoryDto[] = [{ id: "s1", content: "old text", created_at: "2026-08-25T09:00:00Z" }];
    const { a, reads } = assembler(() => current);
    expect(await a.buildSystemPrompt({ ...base })).toContain("old text");
    current = [{ id: "s1", content: "new text", created_at: "2026-08-25T09:00:00Z" }];
    // Inside the 60 s window the cached copy answers …
    expect(await a.buildSystemPrompt({ ...base })).toContain("old text");
    expect(reads()).toBe(1);
    // … until the page says the memories changed.
    a.invalidateServerMemories();
    const after = await a.buildSystemPrompt({ ...base });
    expect(after).toContain("new text");
    expect(after).not.toContain("old text");
    expect(reads()).toBe(2);
  });
});
