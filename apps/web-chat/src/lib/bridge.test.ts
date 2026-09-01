// Bridge client tests — the page side of the Phase 3 transport. Verifies
// that host `turn.event` frames are re-encoded as SSE bytes the unchanged
// parseSSE consumes, that outbound messages carry the nonce, and that a
// missing nonce fails the submit rather than hanging an open stream.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./auth", () => ({ HOSTED_MODE: true }));

import { bridge, startBridge } from "./bridge";
import { parseSSE, type StreamEvent } from "./stream";

interface FakeWebview {
  postMessage: ReturnType<typeof vi.fn>;
  addEventListener: (t: string, cb: (e: { data: unknown }) => void) => void;
  emit: (data: unknown) => void;
}

function installFakeWebview(nonce: string | null): FakeWebview {
  let handler: ((e: { data: unknown }) => void) | null = null;
  const fake: FakeWebview = {
    postMessage: vi.fn(),
    addEventListener: (t, cb) => {
      if (t === "message") handler = cb;
    },
    emit: (data) => handler?.({ data }),
  };
  (window as unknown as { chrome?: unknown }).chrome = { webview: fake };
  if (nonce === null) {
    delete (window as unknown as { __SRAN_BRIDGE__?: unknown }).__SRAN_BRIDGE__;
  } else {
    (window as unknown as { __SRAN_BRIDGE__?: unknown }).__SRAN_BRIDGE__ = { nonce };
  }
  return fake;
}

async function collect(res: Response): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  for await (const ev of parseSSE(res)) events.push(ev);
  return events;
}

const NONCE = "unit-nonce-0123456789";

beforeEach(() => {
  bridge.resetForTest();
});

afterEach(() => {
  delete (window as unknown as { chrome?: unknown }).chrome;
  delete (window as unknown as { __SRAN_BRIDGE__?: unknown }).__SRAN_BRIDGE__;
});

describe("host.query control channel (Phase 5)", () => {
  it("posts host.query with the nonce + op and resolves on the correlated host.result", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const promise = bridge.hostQuery("skills.list");
    const sent = wv.postMessage.mock.calls
      .map((c) => c[0] as { type: string; payload: { requestId: string; op: string } })
      .find((m) => m.type === "host.query");
    expect(sent).toBeTruthy();
    expect(sent!.payload.op).toBe("skills.list");
    // Host echoes the requestId back on host.result.
    wv.emit({
      type: "host.result",
      payload: { requestId: sent!.payload.requestId, ok: true, data: { skills: [] } },
    });
    await expect(promise).resolves.toEqual({ ok: true, data: { skills: [] }, error: undefined });
  });

  it("resolves bridge-unavailable when there is no nonce (no hang)", async () => {
    installFakeWebview(null);
    startBridge();
    await expect(bridge.hostQuery("settings.get")).resolves.toEqual({ ok: false, error: "bridge-unavailable" });
  });

  it("ignores a host.result for an unknown requestId", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    // Must not throw or reject anything.
    expect(() => wv.emit({ type: "host.result", payload: { requestId: "ghost", ok: true } })).not.toThrow();
  });

  it("reportRendererError posts diag.reportRendererError", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    bridge.reportRendererError("error", "boom", "stack-here");
    const sent = wv.postMessage.mock.calls
      .map((c) => c[0] as { type: string; payload: { op: string; args: { message: string } } })
      .find((m) => m.type === "host.query");
    expect(sent!.payload.op).toBe("diag.reportRendererError");
    expect(sent!.payload.args.message).toBe("boom");
  });
});

describe("bridge transport", () => {
  it("posts ui.ready with the nonce on start", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    expect(wv.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ nonce: NONCE, type: "ui.ready" }),
    );
  });

  it("submit posts turn.submit with opaque handles and no path", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const ctrl = new AbortController();
    bridge.submitTurn("conv-1", "hello", ctrl.signal, ["att_1"], "auto");
    const submit = wv.postMessage.mock.calls
      .map((c) => c[0] as { type: string; payload: unknown })
      .find((m) => m.type === "turn.submit");
    expect(submit).toBeTruthy();
    expect(submit).toMatchObject({
      nonce: NONCE,
      type: "turn.submit",
      payload: { conversationId: "conv-1", text: "hello", attachmentHandles: ["att_1"], modelId: "auto" },
    });
    ctrl.abort();
  });

  it("re-encodes host turn.event frames as SSE the renderer parses", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const res = bridge.submitTurn("conv-2", "hi", undefined, [], null);
    // Host streams three events, then completes.
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-2", event: "message_start", data: { message_id: "m1", conversation_id: "conv-2" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-2", event: "content_delta", data: { delta: "Hello " } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-2", event: "content_delta", data: { delta: "world" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-2", event: "message_complete", data: { message_id: "m1" } } });
    const events = await collect(res);
    expect(events.map((e) => e.event)).toEqual([
      "message_start",
      "content_delta",
      "content_delta",
      "message_complete",
    ]);
    expect((events[1].data as { delta: string }).delta).toBe("Hello ");
    expect((events[3].data as { message_id: string }).message_id).toBe("m1");
  });

  it("routes events only to the matching conversation's stream", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const resA = bridge.submitTurn("conv-A", "a", undefined, [], null);
    // An event for a different conversation must not land on conv-A.
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-B", event: "content_delta", data: { delta: "leak?" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-A", event: "content_delta", data: { delta: "mine" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-A", event: "message_complete", data: {} } });
    const events = await collect(resA);
    const deltas = events.filter((e) => e.event === "content_delta").map((e) => (e.data as { delta: string }).delta);
    expect(deltas).toEqual(["mine"]);
  });

  it("terminal error event closes the stream", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const res = bridge.submitTurn("conv-3", "x", undefined, [], null);
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-3", event: "error", data: { code: "boom", message: "nope" } } });
    const events = await collect(res);
    expect(events.at(-1)?.event).toBe("error");
  });

  it("submit without a nonce fails as an unavailable transport (no hang)", async () => {
    installFakeWebview(null);
    startBridge();
    const res = bridge.submitTurn("conv-4", "x", undefined, [], null);
    expect(res.ok).toBe(false);
    expect(res.status).toBe(503);
    // The body is a closed stream, so consuming it terminates immediately.
    const events = await collect(res);
    expect(events).toEqual([]);
  });

  it("cancel posts turn.cancel with the nonce", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    bridge.cancelTurn("conv-5");
    expect(wv.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ nonce: NONCE, type: "turn.cancel", payload: { conversationId: "conv-5" } }),
    );
  });

  it("aborting the signal posts turn.cancel", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const ctrl = new AbortController();
    bridge.submitTurn("conv-6", "x", ctrl.signal, [], null);
    wv.postMessage.mockClear();
    ctrl.abort();
    expect(wv.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "turn.cancel", payload: { conversationId: "conv-6" } }),
    );
  });

  it("delivers consent.request to a registered handler", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const seen: unknown[] = [];
    const off = bridge.onConsentRequest((r) => seen.push(r));
    wv.emit({ type: "consent.request", payload: { promptId: "p1", tool: "write_file", argumentSummary: "path: 12 chars", dangerClass: "danger.fileWrite" } });
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ promptId: "p1", tool: "write_file" });
    off();
  });

  it("tracks the seq cursor from turn.event envelopes", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    bridge.submitTurn("conv-seq", "x", undefined, [], null);
    expect(bridge.peekSeq("conv-seq")).toBe(0);
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-seq", seq: 1, event: "message_start", data: {} } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-seq", seq: 2, event: "content_delta", data: { delta: "hi" } } });
    expect(bridge.peekSeq("conv-seq")).toBe(2);
  });

  it("resumeTurn posts ui.ready with the conversation and last seq", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    // Prime a seq cursor as if a prior turn streamed.
    bridge.submitTurn("conv-r", "x", undefined, [], null);
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-r", seq: 3, event: "content_delta", data: { delta: "abc" } } });
    wv.postMessage.mockClear();
    bridge.resumeTurn("conv-r", undefined);
    expect(wv.postMessage).toHaveBeenCalledWith(
      // FIX41: the cursor is (runId, seq). This host sent no runId, so the
      // page reports null — which the host reads as "same run", i.e. the
      // pre-FIX41 behaviour.
      expect.objectContaining({ nonce: NONCE, type: "ui.ready", payload: { conversationId: "conv-r", lastSeq: 3, runId: null } }),
    );
  });

  // FIX41 — the cursor is the PAIR. A frame from a new run must reset the
  // seq comparison, or every frame of the second turn would be dropped for
  // failing to advance the first turn's count.
  it("a frame from a different run restarts the seq cursor", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const res1 = bridge.submitTurn("conv-runs", "x", undefined, [], null);
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-runs", runId: "run-A", seq: 40, event: "content_delta", data: { delta: "first" } } });
    expect(bridge.peekSeq("conv-runs")).toBe(40);
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-runs", runId: "run-A", seq: 41, event: "message_complete", data: {} } });
    await res1.text();

    // Second turn on the SAME conversation, whose host-side seq happens to
    // start lower than where the first one ended.
    const res2 = bridge.submitTurn("conv-runs", "y", undefined, [], null);
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-runs", runId: "run-B", seq: 1, event: "message_start", data: {} } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-runs", runId: "run-B", seq: 2, event: "content_delta", data: { delta: "second" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-runs", runId: "run-B", seq: 3, event: "message_complete", data: {} } });
    const text = await res2.text();
    expect(text).toContain("second");
    expect(bridge.peekSeq("conv-runs")).toBe(3);
  });

  // FIX41 — `cancelled` is a terminal. Without it the sink stayed open and
  // the page waited on a run that had already ended.
  it("cancelled closes the sink like message_complete does", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const res = bridge.submitTurn("conv-cancel", "x", undefined, [], null);
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-cancel", runId: "r", seq: 1, event: "message_start", data: {} } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-cancel", runId: "r", seq: 2, event: "cancelled", data: { message_id: "m", partial: true } } });
    const text = await res.text();
    expect(text).toContain("event: cancelled");
  });

  it("resume renders the host's replayed events", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    const res = bridge.resumeTurn("conv-rr", undefined);
    // Host replays the buffered turn (as if the page reloaded mid-turn).
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-rr", seq: 1, event: "message_start", data: { message_id: "m9" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-rr", seq: 2, event: "content_delta", data: { delta: "resumed " } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-rr", seq: 3, event: "content_delta", data: { delta: "answer" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-rr", seq: 4, event: "message_complete", data: { message_id: "m9" } } });
    const events = await collect(res);
    expect(events.map((e) => e.event)).toEqual(["message_start", "content_delta", "content_delta", "message_complete"]);
    expect(bridge.peekSeq("conv-rr")).toBe(4);
  });

  it("does NOT advance the resume cursor for frames dropped with no sink (4.11 regression)", async () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    // Frames arrive while there is NO open sink (the reload gap): they must be
    // dropped AND leave the cursor at 0, so a subsequent resume replays from 1.
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-gap", seq: 1, event: "content_delta", data: { delta: "a" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-gap", seq: 2, event: "content_delta", data: { delta: "b" } } });
    expect(bridge.peekSeq("conv-gap")).toBe(0);
    // Now the page resumes: it reports lastSeq 0, the host replays from 1, and
    // every frame renders.
    const res = bridge.resumeTurn("conv-gap", undefined);
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-gap", seq: 1, event: "content_delta", data: { delta: "a" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-gap", seq: 2, event: "content_delta", data: { delta: "b" } } });
    wv.emit({ type: "turn.event", payload: { conversationId: "conv-gap", seq: 3, event: "message_complete", data: {} } });
    const events = await collect(res);
    expect(events.filter((e) => e.event === "content_delta").map((e) => (e.data as { delta: string }).delta)).toEqual(["a", "b"]);
  });

  it("respondConsent posts the decision and scope with the nonce", () => {
    const wv = installFakeWebview(NONCE);
    startBridge();
    bridge.respondConsent("p1", "allow", "once");
    expect(wv.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ nonce: NONCE, type: "consent.respond", payload: { promptId: "p1", decision: "allow", scope: "once" } }),
    );
  });
});
