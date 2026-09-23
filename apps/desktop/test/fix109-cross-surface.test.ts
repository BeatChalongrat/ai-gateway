// FIX109 — one conversation, one owner.
//
// Part A: the local chain (<userData>/history/<id>.json) is a cache of the
// server transcript with tool chains added. Until FIX109 it was read whenever
// it existed, so a web turn taken after the first desktop turn reached the
// page and never the model. Now the file carries the server's message_count
// it was written against, and a load compares.
//
// Part B: the server's conversation state (chat.conversation_states) is
// adopted when the host builds a turn from the server transcript, its
// id-anchors translated to chain indices through the transcript's row ids.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { chainFreshness, countVisibleRows, HISTORY_FILE_VERSION, nextMarker, parseHistoryFile, serializeHistoryFile, unmarkedFreshness } from "../src/main/chain-freshness";
import { ContextManager } from "../src/main/context-manager";
import { adoptServerState, emptyState, MemoryConversationStateStore } from "../src/main/conversation-state";
import type { GatewayMessage } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const quiet: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };
const u = (t: string): GatewayMessage => ({ role: "user", content: t });
const a = (t: string): GatewayMessage => ({ role: "assistant", content: t });
const call = (text: string | null): GatewayMessage => ({ role: "assistant", content: text, tool_calls: [{ id: "c1", type: "function", function: { name: "remember", arguments: "{}" } }] });
const tool = (t: string): GatewayMessage => ({ role: "tool", tool_call_id: "c1", name: "remember", content: t });

/** The chain the live probe produced on turn 1: user → tool-call preamble → tool result → final. */
const DESKTOP_TURN_1: GatewayMessage[] = [u("Remember this code word: PELICAN-41."), call(null), tool("saved"), a("OK")];

describe("FIX109 Part A — the history file and its marker", () => {
  it("a pre-FIX109 bare array parses with no marker; the v2 envelope keeps it; garbage is null", () => {
    expect(parseHistoryFile(DESKTOP_TURN_1)).toEqual({ messages: DESKTOP_TURN_1, serverMessageCount: null });
    const round = parseHistoryFile(JSON.parse(serializeHistoryFile(DESKTOP_TURN_1, 2)));
    expect(round).toEqual({ messages: DESKTOP_TURN_1, serverMessageCount: 2 });
    expect(JSON.parse(serializeHistoryFile([], null))).toEqual({ v: HISTORY_FILE_VERSION, server_message_count: null, messages: [] });
    expect(parseHistoryFile({ v: 2, server_message_count: -1, messages: [] })).toEqual({ messages: [], serverMessageCount: null });
    expect(parseHistoryFile({ v: 2 })).toBeNull();
    expect(parseHistoryFile("nope")).toBeNull();
    expect(parseHistoryFile(null)).toBeNull();
  });

  it("countVisibleRows is what importVisible sent: one user row and at most one assistant row per turn", () => {
    expect(countVisibleRows(DESKTOP_TURN_1)).toBe(2);
    // Preamble text + final answer are imported as ONE assistant row (turn.assistantText).
    expect(countVisibleRows([u("q"), call("Let me check."), tool("r"), a("Done.")])).toBe(2);
    // A turn that produced no text imports only its user row.
    expect(countVisibleRows([u("q"), call(null), tool("r")])).toBe(1);
    // An attachment turn (array content) is still a user row.
    expect(countVisibleRows([{ role: "user", content: [{ type: "text", text: "see" }] } as GatewayMessage, a("ok"), u("more"), a("yes")])).toBe(4);
    expect(countVisibleRows([])).toBe(0);
  });

  it("chainFreshness: equal is fresh, server ahead is stale, server behind keeps the only copy, no marker is unmarked, no server is unknown", () => {
    const marked = { messages: DESKTOP_TURN_1, serverMessageCount: 2 };
    expect(chainFreshness(marked, 2)).toBe("fresh");
    expect(chainFreshness(marked, 4)).toBe("stale");
    expect(chainFreshness(marked, 1)).toBe("fresh");
    expect(chainFreshness({ messages: DESKTOP_TURN_1, serverMessageCount: null }, 2)).toBe("unmarked");
    expect(chainFreshness(marked, undefined)).toBe("unknown");
    expect(chainFreshness(marked, Number.NaN)).toBe("unknown");
  });

  it("an unmarked chain is stale only when the server shows more visible rows than it ever imported", () => {
    const legacy = { messages: DESKTOP_TURN_1, serverMessageCount: null };
    expect(unmarkedFreshness(legacy, 2)).toBe("fresh");
    expect(unmarkedFreshness(legacy, 1)).toBe("fresh"); // an import that failed: local is the fuller copy
    expect(unmarkedFreshness(legacy, 3)).toBe("stale");
  });

  it("the next marker is the count read at the start of the turn plus the rows the import inserted; unknown stays unknown", () => {
    expect(nextMarker(2, 2)).toBe(4);
    expect(nextMarker(2, 0)).toBe(2); // the import failed → next load: server 4 > 2 → re-read
    expect(nextMarker(undefined, 2)).toBeNull();
    expect(nextMarker(null, 2)).toBeNull();
  });
});

// ── the host, with a fake chat-edge ──
type Row = { id: string; role: string; content: string; status: string };
const serverRow = (i: number, role: "user" | "assistant", content: string): Row => ({ id: `m${i}`, role, content, status: "completed" });
/** The live probe's server after the web turn: desktop turn 1 imported, web turn native. */
const SERVER_4: Row[] = [serverRow(1, "user", "Remember this code word: PELICAN-41."), serverRow(2, "assistant", "OK"), serverRow(3, "user", "Remember a second code word: TANGERINE-77."), serverRow(4, "assistant", "OK")];

function host(opts: { rows: Row[] | null; messageCount: number | null; state?: Record<string, unknown> | null; withManager?: boolean; store?: MemoryConversationStateStore }) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix109-"));
  const calls = { conversation: 0, pages: 0, state: 0 };
  const chatEdge = {
    async getConversation(id: string) {
      calls.conversation++;
      return opts.messageCount === null ? null : { id, message_count: opts.messageCount, memory_enabled: true };
    },
    async listMessagesPage() {
      calls.pages++;
      if (opts.rows === null) return null;
      return { messages: opts.rows, hasMore: false };
    },
    async getConversationState() {
      calls.state++;
      return opts.state ?? null;
    },
    async listProjects() {
      return [];
    },
    async listGlobalMemories() {
      return [];
    },
    async listMemories() {
      return [];
    },
  };
  const gw = { listModels: async () => null, async *streamCompletion() { yield { kind: "done", finish_reason: "stop" }; } };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }), status: async () => ({}) };
  const assembler = { buildSystemPromptParts: async () => ({ text: "system", parts: [] }) };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, chatEdge as never, (req, sig) => co.runNested(req, sig), quiet, () => false);
  const store = opts.store ?? new MemoryConversationStateStore();
  const manager = opts.withManager ? new ContextManager({ store, extractor: null, log: quiet, emit: () => {} }) : undefined;
  co = new TurnCoordinator(
    { postTurnEvent: () => {}, post: () => {}, postHostResult: () => {}, postHostError: () => {} } as never,
    gw as never, chatEdge as never, assembler as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")), new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null, ...(manager ? { contextManager: manager } : {}) },
    quiet,
  );
  const priv = co as unknown as {
    buildMessages: (c: string, conv: unknown, text: string) => Promise<{ messages: GatewayMessage[]; source: "local" | "server" }>;
    saveLocalHistory: (c: string, m: GatewayMessage[], n: number | null) => void;
    loadLocalHistory: (c: string) => { messages: GatewayMessage[]; serverMessageCount: number | null } | null;
  };
  const historyFile = path.join(tmp, "history", "c.json");
  const writeHistory = (content: unknown) => {
    fs.mkdirSync(path.dirname(historyFile), { recursive: true });
    fs.writeFileSync(historyFile, JSON.stringify(content));
  };
  const build = async (text = "List every code word.") => {
    const conv = await chatEdge.getConversation("c");
    const built = await priv.buildMessages("c", conv, text);
    return { ...built, history: built.messages.slice(1, -1), roles: built.messages.slice(1).map((m) => m.role) };
  };
  return { priv, build, writeHistory, calls, store, tmp };
}

describe("FIX109 Part A — a stale local chain yields to the server transcript", () => {
  it("BEFORE-shape: marker 2, server at 4 → the web turn reaches the model; tool chains are not carried", async () => {
    const h = host({ rows: SERVER_4, messageCount: 4 });
    h.writeHistory({ v: 2, server_message_count: 2, messages: DESKTOP_TURN_1 });
    const b = await h.build();
    expect(b.source).toBe("server");
    expect(b.history.map((m) => m.content)).toEqual(SERVER_4.map((r) => r.content));
    expect(b.roles).toEqual(["user", "assistant", "user", "assistant", "user"]);
    expect(b.roles).not.toContain("tool");
  });

  it("marker 2, server at 2 → the local chain, tool chain included", async () => {
    const h = host({ rows: SERVER_4.slice(0, 2), messageCount: 2 });
    h.writeHistory({ v: 2, server_message_count: 2, messages: DESKTOP_TURN_1 });
    const b = await h.build();
    expect(b.source).toBe("local");
    expect(b.roles).toEqual(["user", "assistant", "tool", "assistant", "user"]);
    expect(h.calls.pages).toBe(0); // no transcript read on a fresh chain
  });

  it("server behind the marker (an import that failed) keeps the local chain — the only copy of that turn", async () => {
    const h = host({ rows: [], messageCount: 0 });
    h.writeHistory({ v: 2, server_message_count: 2, messages: DESKTOP_TURN_1 });
    const b = await h.build();
    expect(b.source).toBe("local");
    expect(b.roles).toContain("tool");
  });

  it("chat-edge unreachable → the local chain (the transcript could not be read either)", async () => {
    const h = host({ rows: null, messageCount: null });
    h.writeHistory({ v: 2, server_message_count: 2, messages: DESKTOP_TURN_1 });
    const b = await h.build();
    expect(b.source).toBe("local");
    expect(h.calls.pages).toBe(0);
  });

  it("a stale chain whose transcript cannot be read is kept rather than replaced with nothing", async () => {
    const h = host({ rows: null, messageCount: 4 });
    h.writeHistory({ v: 2, server_message_count: 2, messages: DESKTOP_TURN_1 });
    const b = await h.build();
    expect(b.source).toBe("local");
    expect(b.roles).toEqual(["user", "assistant", "tool", "assistant", "user"]);
  });

  it("a pre-FIX109 file is judged against the server's visible rows: covered → kept, uncovered → server", async () => {
    const kept = host({ rows: SERVER_4.slice(0, 2), messageCount: 2 });
    kept.writeHistory(DESKTOP_TURN_1);
    const k = await kept.build();
    expect(k.source).toBe("local");
    expect(kept.calls.pages).toBe(1); // one page-through decides it

    const behind = host({ rows: SERVER_4, messageCount: 4 });
    behind.writeHistory(DESKTOP_TURN_1);
    const b = await behind.build();
    expect(b.source).toBe("server");
    expect(b.history.map((m) => m.content)).toContain("Remember a second code word: TANGERINE-77.");
  });

  it("no local file at all reads the server, as before", async () => {
    const h = host({ rows: SERVER_4, messageCount: 4 });
    const b = await h.build();
    expect(b.source).toBe("server");
    expect(b.history).toHaveLength(4);
  });

  it("save writes the v2 envelope with the marker; load reads it back (and still reads a bare array)", () => {
    const h = host({ rows: [], messageCount: 0 });
    h.priv.saveLocalHistory("c", DESKTOP_TURN_1, 4);
    const raw = JSON.parse(fs.readFileSync(path.join(h.tmp, "history", "c.json"), "utf8"));
    expect(raw).toMatchObject({ v: 2, server_message_count: 4 });
    expect(h.priv.loadLocalHistory("c")).toEqual({ messages: DESKTOP_TURN_1, serverMessageCount: 4 });
    h.priv.saveLocalHistory("c", DESKTOP_TURN_1, null);
    expect(h.priv.loadLocalHistory("c")?.serverMessageCount).toBeNull();
    h.writeHistory(DESKTOP_TURN_1);
    expect(h.priv.loadLocalHistory("c")).toEqual({ messages: DESKTOP_TURN_1, serverMessageCount: null });
  });
});

// ── Part B ──
const SERVER_STATE = {
  conversation_id: "c",
  current_goal: "Keep the user's code words",
  structured_summary: { key_context: "Two code words so far" },
  confirmed_decisions: [{ decision: "reply OK" }],
  active_artifacts: [],
  open_tasks: [],
  rejected_approaches: [],
  unresolved_questions: [],
  summarized_up_to_message_id: "m2",
  message_count_at_summary: 40, // the server's logical counter — NOT an index
  summary_token_count: 57,
  manual_compact_boundary_message_id: null,
  compaction_instructions: "",
  created_at: "2026-09-14T00:00:00Z",
  updated_at: "2026-09-14T01:00:00Z",
};
const EMPTY_ENVELOPE = { conversation_id: "c", current_goal: null, structured_summary: {}, confirmed_decisions: [], active_artifacts: [], open_tasks: [], rejected_approaches: [], unresolved_questions: [] };
const ROW_IDS = SERVER_4.map((r) => r.id);

describe("FIX109 Part B — the server's state, anchored on the transcript", () => {
  it("maps field for field and translates the id anchor to index + 1 through the row ids", () => {
    const s = adoptServerState("c", SERVER_STATE, ROW_IDS)!;
    expect(s).not.toBeNull();
    expect(s.current_goal).toBe("Keep the user's code words");
    expect(s.structured_summary).toEqual({ key_context: "Two code words so far" });
    expect(s.confirmed_decisions).toEqual([{ decision: "reply OK" }]);
    expect(s.summarized_up_to_message_id).toBe("m2");
    expect(s.message_count_at_summary).toBe(2); // rows m1, m2 folded; NOT the server's 40
    expect(s.manual_compact_boundary_index).toBeNull();
    expect(s.summary_token_count).toBe(57);
    expect(s.updated_at).toBe("2026-09-14T01:00:00Z");
  });

  it("an anchor id not among the rows (past the ceiling, another branch, an empty row) becomes 0 — re-fold, never skip", () => {
    expect(adoptServerState("c", { ...SERVER_STATE, summarized_up_to_message_id: "m999" }, ROW_IDS)!.message_count_at_summary).toBe(0);
    expect(adoptServerState("c", { ...SERVER_STATE, summarized_up_to_message_id: null }, ROW_IDS)!.message_count_at_summary).toBe(0);
  });

  it("the manual boundary translates to the row's index, or null when absent", () => {
    expect(adoptServerState("c", { ...SERVER_STATE, manual_compact_boundary_message_id: "m3" }, ROW_IDS)!.manual_compact_boundary_index).toBe(2);
    expect(adoptServerState("c", { ...SERVER_STATE, manual_compact_boundary_message_id: "gone" }, ROW_IDS)!.manual_compact_boundary_index).toBeNull();
  });

  it("the empty envelope (no state row) and malformed blobs adopt nothing / safe defaults", () => {
    expect(adoptServerState("c", EMPTY_ENVELOPE, ROW_IDS)).toBeNull();
    expect(adoptServerState("c", null, ROW_IDS)).toBeNull();
    const odd = adoptServerState("c", { ...SERVER_STATE, structured_summary: "not an object", confirmed_decisions: "nope", open_tasks: [1, "x", { task: "t" }] }, ROW_IDS)!;
    expect(odd.structured_summary).toEqual({});
    expect(odd.confirmed_decisions).toEqual([]);
    expect(odd.open_tasks).toEqual([{ task: "t" }]);
  });

  it("rebase: adopts the server state; resets a local anchor when the server has none; leaves an empty store alone", () => {
    const store = new MemoryConversationStateStore();
    const mgr = new ContextManager({ store, extractor: null, log: quiet, emit: () => {} });
    expect(mgr.rebase("c", ROW_IDS, EMPTY_ENVELOPE)).toBe("none");
    expect(store.get("c")).toBeNull();

    store.put({ ...emptyState("c"), current_goal: "local summary", message_count_at_summary: 7, manual_compact_boundary_index: 5 });
    expect(mgr.rebase("c", ROW_IDS, EMPTY_ENVELOPE)).toBe("reset");
    expect(store.get("c")).toMatchObject({ current_goal: "local summary", message_count_at_summary: 0, manual_compact_boundary_index: null });
    expect(mgr.rebase("c", ROW_IDS, null)).toBe("none"); // idempotent

    expect(mgr.rebase("c", ROW_IDS, SERVER_STATE)).toBe("adopted");
    expect(store.get("c")).toMatchObject({ current_goal: "Keep the user's code words", message_count_at_summary: 2 });
  });

  it("rebase with memory off adopts nothing but still resets a stale local anchor", () => {
    const store = new MemoryConversationStateStore();
    const mgr = new ContextManager({ store, extractor: null, log: quiet, emit: () => {} });
    store.put({ ...emptyState("c"), current_goal: "local", message_count_at_summary: 3 });
    expect(mgr.rebase("c", ROW_IDS, SERVER_STATE, false)).toBe("reset");
    expect(store.get("c")!.current_goal).toBe("local");
  });

  it("through the host: a stale chain rebuilt from the server adopts the server summary at the translated anchor", async () => {
    const h = host({ rows: SERVER_4, messageCount: 4, state: SERVER_STATE, withManager: true });
    h.writeHistory({ v: 2, server_message_count: 2, messages: DESKTOP_TURN_1 });
    h.store.put({ ...emptyState("c"), current_goal: "desktop summary", message_count_at_summary: 4 }); // indexed the OLD chain
    const b = await h.build();
    expect(b.source).toBe("server");
    expect(h.calls.state).toBe(1);
    expect(h.store.get("c")).toMatchObject({ current_goal: "Keep the user's code words", message_count_at_summary: 2 });
  });

  it("through the host: a fresh chain never asks the server for its state", async () => {
    const h = host({ rows: SERVER_4.slice(0, 2), messageCount: 2, state: SERVER_STATE, withManager: true });
    h.writeHistory({ v: 2, server_message_count: 2, messages: DESKTOP_TURN_1 });
    await h.build();
    expect(h.calls.state).toBe(0);
    expect(h.store.get("c")).toBeNull();
  });
});
