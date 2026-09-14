// FIX116 — the store driven through the REAL bridge over a fake host.
//
// FIX114's store test mocked the bridge and handed every reader its own
// stream, so the condition that broke on the owner's e48 — the bridge's one
// sink per conversation being closed by a stale abort — could not occur in
// the test. Here the bridge is the real one: frames arrive as the host
// posts them, one sink per conversation, and the resume probe and the send
// share it exactly as in the desktop shell.
//
//   Part A — a send inside the resume probe's 700 ms window: the probe's
//            stand-down must not end the send's stream. Measured on e48
//            before the fix: "Network error during streaming." at ~1 s and
//            the answer never rendered until a reload (3 of 3 runs).
//   Part B — a turn that ends while the model is still "thinking" must not
//            keep is_thinking on: the header would read "Thinking…" forever
//            under a cancelled or failed turn. The reasoning text stays.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

vi.mock("@/lib/auth", async (importOriginal) => ({ ...(await importOriginal<object>()), HOSTED_MODE: true }));
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { api } from "@/lib/api";
import { bridge, startBridge } from "@/lib/bridge";
import { useChatStore } from "./chatStore";

interface FakeWebview {
  postMessage: ReturnType<typeof vi.fn>;
  addEventListener: (t: string, cb: (e: { data: unknown }) => void) => void;
  emit: (data: unknown) => void;
}
function installFakeWebview(nonce: string): FakeWebview {
  let handler: ((e: { data: unknown }) => void) | null = null;
  const fake: FakeWebview = {
    postMessage: vi.fn(),
    addEventListener: (t, cb) => {
      if (t === "message") handler = cb;
    },
    emit: (data) => handler?.({ data }),
  };
  (window as unknown as { chrome?: unknown }).chrome = { webview: fake };
  (window as unknown as { __SRAN_BRIDGE__?: unknown }).__SRAN_BRIDGE__ = { nonce };
  return fake;
}
const turnEvent = (conversationId: string, seq: number, event: string, data: unknown) => ({
  type: "turn.event",
  payload: { conversationId, runId: "run-1", seq, event, data },
});
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const until = async (pred: () => boolean, ms = 3000) => {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await sleep(20);
  }
};
function conv(id: string): Conversation {
  return { id, title: "T", model_id: "m", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
const initialState = useChatStore.getState();
const postedTypes = (wv: FakeWebview) => wv.postMessage.mock.calls.map((c) => (c[0] as { type: string }).type);
const asst = (convId: string) => (useChatStore.getState().messages[convId] ?? []).filter((m) => m.role === "assistant");

let wv: FakeWebview;
beforeEach(() => {
  bridge.resetForTest();
  wv = installFakeWebview("unit-nonce-fix116-store");
  startBridge();
  useChatStore.setState({ ...initialState, conversations: [conv("c1")], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
  // The store's side calls after a turn; the streams themselves stay real (bridge).
  api.getConversation = vi.fn(async () => conv("c1"));
  api.generateTitle = vi.fn(async () => ({ title: "t" }));
  api.listMessages = vi.fn(async () => ({ messages: [], has_more: false }));
  api.cancelGeneration = vi.fn(async () => ({ cancelled: true }));
  // Asynchronous on purpose: the store's scheduleFlush stores the handle
  // AFTER calling requestAnimationFrame, so a stub that runs the callback
  // synchronously leaves the handle set and no later delta ever flushes.
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { setTimeout(() => cb(0), 0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as unknown as { chrome?: unknown }).chrome;
  delete (window as unknown as { __SRAN_BRIDGE__?: unknown }).__SRAN_BRIDGE__;
});

describe("FIX116 Part A — a send inside the resume probe window (real bridge)", () => {
  it("the probe's stand-down does not end the live turn: the answer streams and completes, no cancel is posted", async () => {
    useChatStore.getState().hostedResumeProbe("c1"); // opening the conversation
    await until(() => postedTypes(wv).includes("ui.ready"));
    const p = useChatStore.getState().sendMessage("c1", "typed right away"); // < 700 ms later
    await until(() => postedTypes(wv).includes("turn.submit"));
    await sleep(900); // past the probe window: the probe stands down (aborts its own controller)
    const midway = asst("c1");
    expect(midway).toHaveLength(1);
    expect(midway[0]!.isStreaming).toBe(true); // still waiting for the host — not "Network error during streaming."
    expect(midway[0]!.error_code).toBeUndefined();
    wv.emit(turnEvent("c1", 1, "message_start", { message_id: "a1", conversation_id: "c1" }));
    wv.emit(turnEvent("c1", 2, "content_delta", { delta: "the answer" }));
    wv.emit(turnEvent("c1", 3, "message_complete", { message_id: "a1" }));
    await p;
    const rows = asst("c1");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ status: "completed", isStreaming: false, content: "the answer" });
    expect(rows[0]!.error_code).toBeUndefined();
    expect(postedTypes(wv).filter((t) => t === "turn.cancel")).toHaveLength(0);
    expect(useChatStore.getState().streamingAssistantId["c1"]).toBeUndefined();
  });

  it("Stop on that turn still reaches the host", async () => {
    useChatStore.getState().hostedResumeProbe("c1");
    await until(() => postedTypes(wv).includes("ui.ready"));
    const p = useChatStore.getState().sendMessage("c1", "typed right away");
    await until(() => postedTypes(wv).includes("turn.submit"));
    await sleep(900);
    wv.emit(turnEvent("c1", 1, "message_start", { message_id: "a1", conversation_id: "c1" }));
    wv.emit(turnEvent("c1", 2, "content_delta", { delta: "half" }));
    await until(() => asst("c1")[0]?.content === "half");
    void useChatStore.getState().cancelGeneration("c1");
    await p;
    expect(postedTypes(wv).filter((t) => t === "turn.cancel")).toHaveLength(1);
    expect(asst("c1")[0]).toMatchObject({ status: "cancelled", isStreaming: false, content: "half" });
  });
});

describe("FIX116 Part B — a finished turn is never still thinking", () => {
  // Returns the turn promise wrapped: an async function returning a promise
  // would be flattened by the caller's await and wait for the whole turn.
  async function thinkingTurn(): Promise<{ turn: Promise<void> }> {
    const p = useChatStore.getState().sendMessage("c1", "think about it");
    await until(() => postedTypes(wv).includes("turn.submit"));
    wv.emit(turnEvent("c1", 1, "message_start", { message_id: "a1", conversation_id: "c1" }));
    wv.emit(turnEvent("c1", 2, "analysis_status", { status: "started" }));
    wv.emit(turnEvent("c1", 3, "reasoning_delta", { delta: "step one" }));
    await until(() => asst("c1")[0]?.is_thinking === true && asst("c1")[0]?.reasoning_content === "step one");
    return { turn: p };
  }

  it("Stop while the model is thinking: is_thinking off, reasoning text kept, status cancelled", async () => {
    const { turn } = await thinkingTurn();
    void useChatStore.getState().cancelGeneration("c1");
    await turn;
    expect(asst("c1")[0]).toMatchObject({ status: "cancelled", isStreaming: false, is_thinking: false, reasoning_content: "step one" });
  });

  it("an error while the model is thinking: is_thinking off, reasoning text kept, the error still shown", async () => {
    const { turn } = await thinkingTurn();
    wv.emit(turnEvent("c1", 4, "error", { code: "provider_error", message: "upstream 502", retryable: true }));
    await turn;
    expect(asst("c1")[0]).toMatchObject({ status: "error", isStreaming: false, is_thinking: false, reasoning_content: "step one", error_code: "provider_error" });
  });

  it("a superseded turn that was thinking: is_thinking off on the retired row", async () => {
    const { turn: p1 } = await thinkingTurn();
    const p2 = useChatStore.getState().sendMessage("c1", "never mind, next question");
    await until(() => postedTypes(wv).filter((t) => t === "turn.submit").length === 2);
    await p1;
    const rows = asst("c1");
    expect(rows[0]).toMatchObject({ status: "cancelled", isStreaming: false, is_thinking: false, reasoning_content: "step one" });
    wv.emit(turnEvent("c1", 1, "message_start", { message_id: "a2", conversation_id: "c1" }));
    wv.emit(turnEvent("c1", 2, "message_complete", { message_id: "a2" }));
    await p2;
    expect(asst("c1")[1]!.status).toBe("completed");
  });
});
