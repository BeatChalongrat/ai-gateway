// FIX20 §6.5 — hosted mode is a RUNTIME decision: true exactly when the
// Electron preload's `window.sranHost` surface (postMessage + addEventListener)
// is present. No build flag is consulted.
import { describe, expect, it } from "vitest";

import { detectHostedMode } from "./auth";

const surface = { surfaceVersion: 2, nonce: "n", postMessage() {}, addEventListener() {} };

describe("detectHostedMode", () => {
  it("is false with no window (server render) and in a plain browser", () => {
    expect(detectHostedMode(undefined)).toBe(false);
    expect(detectHostedMode({})).toBe(false);
    expect(detectHostedMode({ sranHost: null })).toBe(false);
  });

  it("is true only for the real preload surface shape", () => {
    expect(detectHostedMode({ sranHost: surface })).toBe(true);
    // a lookalike without the two functions is not a host
    expect(detectHostedMode({ sranHost: { nonce: "n" } })).toBe(false);
    expect(detectHostedMode({ sranHost: { postMessage() {} } })).toBe(false);
  });

  it("ignores NEXT_PUBLIC_HOSTED entirely", () => {
    const prev = process.env.NEXT_PUBLIC_HOSTED;
    process.env.NEXT_PUBLIC_HOSTED = "1";
    try {
      expect(detectHostedMode({})).toBe(false);
    } finally {
      if (prev === undefined) delete process.env.NEXT_PUBLIC_HOSTED;
      else process.env.NEXT_PUBLIC_HOSTED = prev;
    }
  });

  it("reads the live jsdom window by default (no host here)", () => {
    expect(detectHostedMode()).toBe(false);
  });
});
