// FIX58 — `remember` writes the server now, and which store a note lands
// in is decided by its scope.
//
// The two questions this pins:
//   1. scope routing — a global note leaves the machine (chat-edge's
//      chat.user_memories, the store the web client already writes);
//      a conversation-scoped note stays with the sidecar, because the
//      prompt assembler reads those back locally and the server's
//      injection query never returns them.
//   2. honesty on failure — a save that did not reach the server must
//      come back as an Error, so the model cannot tell the user it
//      remembered something it did not (the FIX5 §1 / FIX22 §1a rule,
//      which now has a network in front of it).
import { describe, expect, it } from "vitest";
import { rememberScopeOf } from "../src/main/turn-coordinator";
import { HostTools } from "../src/main/host-tools";

describe("rememberScopeOf", () => {
  it("routes an explicit conversation note to the local store", () => {
    expect(rememberScopeOf({ scope: "conversation" })).toBe("conversation");
    expect(rememberScopeOf({ scope: "CONVERSATION" })).toBe("conversation");
  });

  it("routes an explicit global note to the server", () => {
    expect(rememberScopeOf({ scope: "global" })).toBe("global");
  });

  // The tool schema's own default is global, and global is the store the
  // user can see and delete on the web. An unknown scope erring towards
  // the visible store is the safer of the two mistakes.
  it("treats a missing or unknown scope as global", () => {
    expect(rememberScopeOf({})).toBe("global");
    expect(rememberScopeOf({ scope: "" })).toBe("global");
    expect(rememberScopeOf({ scope: "nonsense" })).toBe("global");
    expect(rememberScopeOf({ scope: 42 })).toBe("global");
  });
});

type CreateArgs = Parameters<import("../src/main/chat-edge-client").ChatEdgeClient["createMemory"]>[0];

function toolsWith(
  createMemory: (a: CreateArgs) => Promise<unknown>,
  conversation: { project_id?: string | null } | null,
): { tools: HostTools; calls: CreateArgs[] } {
  const calls: CreateArgs[] = [];
  const chatEdge = {
    getConversation: async () => conversation,
    createMemory: async (a: CreateArgs) => {
      calls.push(a);
      return createMemory(a);
    },
  };
  const log = { info() {}, warn() {}, error() {} };
  const tools = new HostTools(
    {} as never,
    chatEdge as never,
    (async () => ({})) as never,
    log as never,
    () => false,
  );
  return { tools, calls };
}

// FIX130 — these pin scope routing and failure honesty for a save the user
// ASKED for; a note nobody asked for becomes a pending suggestion instead
// (fix130-turn-memory.test.ts).
const ctx = () => ({
  conversationId: "11111111-1111-4111-8111-111111111111",
  toolCallId: "tc1",
  signal: new AbortController().signal,
  userMessage: "Please remember this.",
});

describe("remember (host tool)", () => {
  it("saves a general chat's note to the user's global memory", async () => {
    const { tools, calls } = toolsWith(
      async () => ({ ok: true, id: "m1", content: "The staging box is blue." }),
      { project_id: null },
    );
    const r = await tools.execute("remember", { note: "The staging box is blue.", scope: "global" }, ctx());
    expect(r.ok).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0].scopeType).toBe("global_user");
    expect(calls[0].scopeId ?? null).toBeNull();
  });

  // A fact learned while working on one client's project belongs to that
  // project. Saving it globally would put it into every other chat —
  // the cross-project leak, arriving from the write side.
  it("saves a project chat's note into that project", async () => {
    const { tools, calls } = toolsWith(
      async () => ({ ok: true, id: "m2", content: "The client contact is Somchai." }),
      { project_id: "22222222-2222-4222-8222-222222222222" },
    );
    const r = await tools.execute("remember", { note: "The client contact is Somchai.", scope: "global" }, ctx());
    expect(r.ok).toBe(true);
    expect(calls[0].scopeType).toBe("project");
    expect(calls[0].scopeId).toBe("22222222-2222-4222-8222-222222222222");
    expect(r.text).toContain("this project's memory");
  });

  it("reports a refused save as an error, never as a save", async () => {
    const { tools } = toolsWith(
      async () => ({ ok: false, status: 422, detail: "content matches guardrail rule R-12" }),
      { project_id: null },
    );
    const r = await tools.execute("remember", { note: "something blocked", scope: "global" }, ctx());
    expect(r.ok).toBe(false);
    expect(r.text).toMatch(/^Error:/);
    expect(r.text).toContain("NOT saved");
    expect(r.text).toContain("guardrail rule R-12");
  });

  // Offline is the case that must not be papered over: telling the user
  // it was saved when it never left the machine is the exact lie the
  // memory-off refusal was written to prevent.
  it("says plainly that an unreachable server means nothing was saved", async () => {
    const { tools } = toolsWith(
      async () => ({ ok: false, status: 0, detail: "fetch failed" }),
      { project_id: null },
    );
    const r = await tools.execute("remember", { note: "offline note", scope: "global" }, ctx());
    expect(r.ok).toBe(false);
    expect(r.text).toContain("NOT saved");
    expect(r.text).toContain("could not be reached");
    expect(r.text).not.toMatch(/\bSaved\b/);
  });

  it("rejects an empty note without calling the server", async () => {
    const { tools, calls } = toolsWith(async () => ({ ok: true, id: "x", content: "" }), null);
    const r = await tools.execute("remember", { note: "   " }, ctx());
    expect(r.ok).toBe(false);
    expect(calls).toHaveLength(0);
  });
});
