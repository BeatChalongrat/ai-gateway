// FIX34 Part A — the quick-ask flag rides turn.submit only while /quick is mounted.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./auth", () => ({ HOSTED_MODE: true }));

import { bridge, startBridge } from "./bridge";

const NONCE = "unit-nonce-quick";

function installFakeWebview() {
  const fake = { postMessage: vi.fn(), addEventListener: vi.fn() };
  (window as unknown as { chrome?: unknown }).chrome = { webview: fake };
  (window as unknown as { __SRAN_BRIDGE__?: unknown }).__SRAN_BRIDGE__ = { nonce: NONCE };
  return fake;
}

function lastSubmit(fake: { postMessage: ReturnType<typeof vi.fn> }) {
  const calls = fake.postMessage.mock.calls.map((c) => c[0] as { type: string; payload: Record<string, unknown> });
  return calls.filter((c) => c.type === "turn.submit").pop()!.payload;
}

beforeEach(() => bridge.resetForTest());
afterEach(() => {
  delete (window as unknown as { chrome?: unknown }).chrome;
  delete (window as unknown as { __SRAN_BRIDGE__?: unknown }).__SRAN_BRIDGE__;
});

describe("quick-ask turns", () => {
  it("carries quick:false by default and quick:true while the quick page is mounted", () => {
    const wv = installFakeWebview();
    startBridge();
    bridge.submitTurn("c1", "hello", undefined, [], null);
    expect(lastSubmit(wv).quick).toBe(false);
    bridge.setQuickAsk(true);
    bridge.submitTurn("c1", "hello again", undefined, [], null);
    expect(lastSubmit(wv).quick).toBe(true);
    bridge.setQuickAsk(false);
    bridge.submitTurn("c1", "back", undefined, [], null);
    expect(lastSubmit(wv).quick).toBe(false);
  });

  it("resetForTest clears the flag", () => {
    bridge.setQuickAsk(true);
    bridge.resetForTest();
    expect(bridge.isQuickAsk).toBe(false);
  });
});
