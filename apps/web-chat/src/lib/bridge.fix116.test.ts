// FIX116 Part A — a sink's abort ends that sink, never the one that replaced it.
//
// The bridge keeps ONE sink per conversation ("one turn per conversation at
// a time"): opening a new sink closes the old one. Before FIX116 the abort
// listener each sink registered called closeSink(conversationId) — by id —
// so an abort arriving AFTER the sink had been replaced closed the
// replacement: the resume probe's stand-down (FIX114) closed the live
// turn's sink, and a stale submit abort would also have posted turn.cancel
// for a run that was no longer its own.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./auth", () => ({ HOSTED_MODE: true }));

import { bridge, startBridge } from "./bridge";
import { parseSSE, type StreamEvent } from "./stream";

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

async function collect(res: Response): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const ev of parseSSE(res)) events.push(ev);
  return events;
}

const NONCE = "unit-nonce-fix116";
const turnEvent = (conversationId: string, seq: number, event: string, data: unknown) => ({
  type: "turn.event",
  payload: { conversationId, runId: "run-1", seq, event, data },
});
const cancelsPosted = (wv: FakeWebview) =>
  wv.postMessage.mock.calls.filter((c) => (c[0] as { type: string }).type === "turn.cancel").length;

beforeEach(() => {
  bridge.resetForTest();
});
afterEach(() => {
  delete (window as unknown as { chrome?: unknown }).chrome;
  delete (window as unknown as { __SRAN_BRIDGE__?: unknown }).__SRAN_BRIDGE__;
});

describe("FIX116 — a replaced sink's abort does not touch the current sink", () => {
  it("resume probe replaced by a submit: aborting the probe leaves the submit's stream open and posts no cancel", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const probeCtrl = new AbortController();
    bridge.resumeTurn("conv-a", probeCtrl.signal); // sink A (the probe)
    const sendCtrl = new AbortController();
    const live = bridge.submitTurn("conv-a", "hello", sendCtrl.signal, [], null); // sink B replaces A
    wv.postMessage.mockClear();
    probeCtrl.abort(); // FIX114's stand-down — the probe found a send owning the slot
    wv.emit(turnEvent("conv-a", 1, "message_start", { message_id: "m1", conversation_id: "conv-a" }));
    wv.emit(turnEvent("conv-a", 2, "content_delta", { delta: "answer" }));
    wv.emit(turnEvent("conv-a", 3, "message_complete", { message_id: "m1" }));
    const events = await collect(live);
    expect(events.map((e) => e.event)).toEqual(["message_start", "content_delta", "message_complete"]);
    expect(cancelsPosted(wv)).toBe(0);
  });

  it("submit replaced by a newer submit: the stale abort posts no turn.cancel and the newer stream stays open", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const oldCtrl = new AbortController();
    bridge.submitTurn("conv-b", "first", oldCtrl.signal, [], null);
    const newCtrl = new AbortController();
    const live = bridge.submitTurn("conv-b", "second", newCtrl.signal, [], null);
    wv.postMessage.mockClear();
    oldCtrl.abort(); // e.g. the first turn's image-generation timeout firing late
    expect(cancelsPosted(wv)).toBe(0); // the host's current run is the second turn's — not ours to cancel
    wv.emit(turnEvent("conv-b", 1, "message_start", { message_id: "m2", conversation_id: "conv-b" }));
    wv.emit(turnEvent("conv-b", 2, "message_complete", { message_id: "m2" }));
    const events = await collect(live);
    expect(events.map((e) => e.event)).toEqual(["message_start", "message_complete"]);
  });

  it("aborting the CURRENT submit still closes its stream and posts turn.cancel (Stop keeps working)", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const ctrl = new AbortController();
    const live = bridge.submitTurn("conv-c", "x", ctrl.signal, [], null);
    wv.postMessage.mockClear();
    ctrl.abort();
    expect(cancelsPosted(wv)).toBe(1);
    expect(wv.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "turn.cancel", payload: { conversationId: "conv-c" } }));
    const events = await collect(live);
    expect(events).toEqual([]); // closed, nothing more arrives
  });

  it("opening a newer sink still ends the older stream (one turn per conversation is kept)", async () => {
    installFakeWebview(NONCE);
    startBridge();
    const old = bridge.submitTurn("conv-d", "first", undefined, [], null);
    bridge.submitTurn("conv-d", "second", undefined, [], null);
    const events = await collect(old);
    expect(events).toEqual([]);
  });
});
