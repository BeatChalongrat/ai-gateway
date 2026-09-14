// FIX114 Part B — who owns "busy"?
//
// Three turn shapes, as the desktop host produces them, driven through
// sendMessage with a controllable stream:
//   control     — message_start, deltas, message_complete
//   failed-tool — message_start, tool_activity started/finished, error
//                 (the host's exact sequence for the turn where the second
//                 model call failed after `remember`, 2026-09-14 07:04Z)
//   superseded  — a second send while the first is still open; the host
//                 aborts the old run and writes NO terminal for it (its own
//                 rule), and the bridge closes the old sink
// Counted after each: assistant rows still streaming, running tool rows,
// and the streaming slot — every one of them a "Working…" on screen.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi, mockBridge } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openResumeStream: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(async () => ({ title: "t" })),
    listMessages: vi.fn(async () => ({ messages: [] })),
    cancelGeneration: vi.fn(async () => {}),
  },
  mockBridge: { seq: 0, peekSeq: vi.fn(() => 0), respondConsent: vi.fn(), respondQuestion: vi.fn(), cancelTurn: vi.fn(), post: vi.fn(() => true) },
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/lib/auth", async (importOriginal) => ({ ...(await importOriginal<object>()), HOSTED_MODE: true }));
vi.mock("@/lib/bridge", () => ({ bridge: mockBridge }));
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
function conv(id: string): Conversation {
  return { id, title: "T", model_id: "m", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  return { response: { ok: true, status: 200, body: stream } as unknown as Response, push: (raw: string) => controller.enqueue(enc.encode(raw)), close: () => controller.close() };
}
const until = async (pred: () => boolean, ms = 3000) => {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await new Promise((r) => setTimeout(r, 20));
  }
};
/** What the screen would show as "Working…": every assistant row still streaming
 *  with no content, plus every tool row still "started". */
function busy(convId: string) {
  const rows = useChatStore.getState().messages[convId] ?? [];
  const asst = rows.filter((m) => m.role === "assistant");
  return {
    streaming_rows: asst.filter((m) => m.isStreaming).length,
    working_indicators: asst.filter((m) => m.isStreaming && m.content.length === 0).length,
    running_tool_rows: asst.flatMap((m) => m.tool_activities ?? []).filter((a) => a.phase === "started").length,
    slot: useChatStore.getState().streamingAssistantId[convId] ?? null,
    statuses: asst.map((m) => m.status),
  };
}

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversations: [conv("c1")], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockResolvedValue(conv("c1"));
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX114 — busy indicators after each turn shape", () => {
  it("control: a normal turn ends with nothing busy", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "hello");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("content_delta", { delta: "hi" }));
    s.push(frame("message_complete", { message_id: "a1" }));
    s.close();
    await p;
    expect(busy("c1")).toEqual({ streaming_rows: 0, working_indicators: 0, running_tool_rows: 0, slot: null, statuses: ["completed"] });
  });

  it("failed after a tool: the error ends the turn; the tool row is finished; the failure is still shown", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "รหัสลับคำที่หนึ่งคือ MANGO-17");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("tool_activity", { id: "t1", tool: "remember", phase: "started", argsSummary: "MANGO-17" }));
    s.push(frame("tool_activity", { id: "t1", tool: "remember", phase: "finished", success: true, durationMs: 45 }));
    s.push(frame("error", { code: "unclassified_error", message: "Something went wrong while the reply was being received", retryable: true }));
    s.close();
    await p;
    const b = busy("c1");
    expect(b.streaming_rows).toBe(0);
    expect(b.working_indicators).toBe(0);
    expect(b.running_tool_rows).toBe(0);
    expect(b.statuses).toEqual(["error"]);
    const asst = useChatStore.getState().messages["c1"]!.find((m) => m.role === "assistant")!;
    expect(asst.error_code).toBe("unclassified_error"); // the failure is not silenced
  });

  it("failed after a tool that never reported finishing: the error also closes the running tool row", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "run it");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.push(frame("tool_activity", { id: "t1", tool: "run_command", phase: "started", argsSummary: "dir" }));
    s.push(frame("error", { code: "host_exception", message: "The turn ended unexpectedly.", retryable: true }));
    s.close();
    await p;
    const b = busy("c1");
    expect(b.streaming_rows).toBe(0);
    expect(b.running_tool_rows).toBe(0); // a tool the host never finished cannot still be "running" after the turn died
    const act = useChatStore.getState().messages["c1"]!.find((m) => m.role === "assistant")!.tool_activities![0]!;
    expect(act.phase).toBe("finished");
    expect(act.success).toBe(false);
  });

  it("superseded: a second send retires the first turn's row — one streaming row, one owner", async () => {
    const s1 = makeStream();
    const s2 = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s1.response).mockResolvedValueOnce(s2.response);
    const p1 = useChatStore.getState().sendMessage("c1", "first question");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s1.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s1.push(frame("content_delta", { delta: "half an answ" }));
    await until(() => (useChatStore.getState().messages["c1"] ?? []).some((m) => m.content.startsWith("half")));
    // The user sends again. The host cancels the old run and writes it no
    // terminal; the bridge closes the old sink ("one turn per conversation").
    const p2 = useChatStore.getState().sendMessage("c1", "second question");
    await until(() => mockApi.openMessageStream.mock.calls.length === 2);
    s1.close();
    await p1;
    const mid = busy("c1");
    expect(mid.streaming_rows).toBe(1); // only the second turn is live
    s2.push(frame("message_start", { message_id: "a2", conversation_id: "c1" }));
    s2.push(frame("content_delta", { delta: "second answer" }));
    s2.push(frame("message_complete", { message_id: "a2" }));
    s2.close();
    await p2;
    const b = busy("c1");
    expect(b.streaming_rows).toBe(0);
    expect(b.working_indicators).toBe(0);
    expect(b.slot).toBeNull();
    // The first row is not silently "completed": it says it was cut short, and keeps what it had.
    const rows = useChatStore.getState().messages["c1"]!.filter((m) => m.role === "assistant");
    expect(rows.map((m) => m.status)).toEqual(["cancelled", "completed"]);
    expect(rows[0]!.content).toBe("half an answ");
    // Stop still works for the live turn: the second turn's controller was not deleted by the first reader's exit.
    expect(useChatStore.getState().abortControllers["c1"]).toBeUndefined(); // both finished → cleared
  });

  it("a send inside the resume probe's window: the send owns the slot, the probe stands down, no second row", async () => {
    // Opening a conversation starts a 700 ms probe for a run to replay. A
    // user who types at once lands a turn inside that window; its frames
    // advance the cursor, and the probe used to take that as "a run
    // replayed", add its own streaming row and steal the slot — the
    // "Working…" measured under a finished answer on e48.
    const probe = makeStream();
    const send = makeStream();
    mockApi.openResumeStream.mockResolvedValueOnce(probe.response);
    mockApi.openMessageStream.mockResolvedValueOnce(send.response);
    mockBridge.peekSeq.mockReturnValueOnce(0); // before the probe waits
    useChatStore.getState().hostedResumeProbe("c1");
    await until(() => mockApi.openResumeStream.mock.calls.length === 1);
    const p = useChatStore.getState().sendMessage("c1", "typed right away");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    mockBridge.peekSeq.mockReturnValue(5); // the send's frames advanced the cursor
    send.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    send.push(frame("content_delta", { delta: "answer" }));
    await new Promise((r) => setTimeout(r, 900)); // past the probe window
    probe.close(); // the bridge closes the probe's sink when the send opened its own
    send.push(frame("message_complete", { message_id: "a1" }));
    send.close();
    await p;
    await new Promise((r) => setTimeout(r, 50));
    const rows = useChatStore.getState().messages["c1"]!.filter((m) => m.role === "assistant");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ status: "completed", isStreaming: false, content: "answer" });
    expect(busy("c1")).toMatchObject({ streaming_rows: 0, working_indicators: 0, slot: null });
  });

  it("a stream that ends with no terminal at all is an error, not a forever-spinner", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValueOnce(s.response);
    const p = useChatStore.getState().sendMessage("c1", "hello");
    await until(() => mockApi.openMessageStream.mock.calls.length === 1);
    s.push(frame("message_start", { message_id: "a1", conversation_id: "c1" }));
    s.close();
    await p;
    const b = busy("c1");
    expect(b.streaming_rows).toBe(0);
    expect(b.statuses).toEqual(["error"]);
  });
});
