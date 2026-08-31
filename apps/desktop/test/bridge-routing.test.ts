// FIX34 Part A — outbound routing with two windows (main + quick-ask). Pins
// "reply to whoever asked": turn.event follows the submitter, host.result the
// requester, everything else goes to main; a dead window falls back to main.
import { describe, expect, it } from "vitest";
import { OutboundRouter } from "../src/main/bridge-routing";

const MAIN = 1;
const QUICK = 7;

function twoWindows(): OutboundRouter {
  const r = new OutboundRouter();
  r.registerWindow(MAIN, { main: true });
  r.registerWindow(QUICK, { main: false });
  return r;
}

describe("OutboundRouter", () => {
  it("turn.event goes to the window that submitted the conversation", () => {
    const r = twoWindows();
    r.noteInbound(QUICK, { type: "turn.submit", payload: { conversationId: "c1" } });
    r.noteInbound(MAIN, { type: "turn.submit", payload: { conversationId: "c2" } });
    expect(r.targetFor("turn.event", { conversationId: "c1", seq: 1 })).toBe(QUICK);
    expect(r.targetFor("turn.event", { conversationId: "c2", seq: 1 })).toBe(MAIN);
  });

  it("an unclaimed conversation's events go to main (pre-FIX34 behaviour)", () => {
    const r = twoWindows();
    expect(r.targetFor("turn.event", { conversationId: "nobody", seq: 1 })).toBe(MAIN);
  });

  it("ui.ready (resume) re-claims a conversation for the resuming window", () => {
    const r = twoWindows();
    r.noteInbound(QUICK, { type: "turn.submit", payload: { conversationId: "c1" } });
    r.noteInbound(MAIN, { type: "ui.ready", payload: { conversationId: "c1", lastSeq: 3 } });
    expect(r.targetFor("turn.event", { conversationId: "c1", seq: 4 })).toBe(MAIN);
    expect(r.ownerOf("c1")).toBe(MAIN);
  });

  it("host.result goes to the requester once, then the entry is forgotten", () => {
    const r = twoWindows();
    r.noteInbound(QUICK, { type: "host.query", payload: { requestId: "q1", op: "shell.prefs.get" } });
    expect(r.targetFor("host.result", { requestId: "q1", ok: true })).toBe(QUICK);
    expect(r.targetFor("host.result", { requestId: "q1", ok: true })).toBe(MAIN); // forgotten → main
  });

  it("consent, notices and errors go to MAIN even when quick asked last", () => {
    const r = twoWindows();
    r.noteInbound(QUICK, { type: "turn.submit", payload: { conversationId: "c1" } });
    expect(r.targetFor("consent.request", { conversationId: "c1", promptId: "p" })).toBe(MAIN);
    expect(r.targetFor("host.notice", { kind: "update-available" })).toBe(MAIN);
    expect(r.targetFor("host.error", { code: "x" })).toBe(MAIN);
  });

  it("a destroyed quick window falls back to main; no windows → null (drop)", () => {
    const r = twoWindows();
    r.noteInbound(QUICK, { type: "turn.submit", payload: { conversationId: "c1" } });
    r.noteInbound(QUICK, { type: "host.query", payload: { requestId: "q1" } });
    r.unregisterWindow(QUICK);
    expect(r.targetFor("turn.event", { conversationId: "c1" })).toBe(MAIN);
    expect(r.targetFor("host.result", { requestId: "q1" })).toBe(MAIN);
    r.unregisterWindow(MAIN);
    expect(r.targetFor("turn.event", { conversationId: "c1" })).toBeNull();
    expect(r.targetFor("host.notice", {})).toBeNull();
  });

  it("ignores malformed payloads without throwing", () => {
    const r = twoWindows();
    r.noteInbound(QUICK, { type: "turn.submit", payload: null });
    r.noteInbound(QUICK, { type: "host.query", payload: { requestId: 42 } });
    r.noteInbound(QUICK, { type: "something.else", payload: { conversationId: "c9" } });
    expect(r.targetFor("turn.event", null)).toBe(MAIN);
    expect(r.targetFor("turn.event", { conversationId: "c9" })).toBe(MAIN);
  });
});
