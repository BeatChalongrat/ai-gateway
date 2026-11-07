// PINNING TEST — senderFrame validation (E1 §2: "validate event.senderFrame
// on every IPC message — by origin, not startsWith").
//
// Pins: a null/destroyed frame, a sub-frame, a non-http frame and any origin
// outside the allow-list are untrusted; only the top frame of an allowed
// origin is trusted. The prefix trick is refused.
//
// LIMITS — what this test cannot see:
//   * isTrustedSender is pure; the wiring that feeds it event.senderFrame is
//     in main/index.ts (guardedHandle) and is pinned only by the source scan
//     in no-remote-no-ipcrenderer.test.ts. With zero channels in E1 there is
//     no live message to exercise it; E3 must add a renderer-side test.
import { describe, expect, it } from "vitest";
import { isTrustedSender } from "../src/main/ipc-guard";

const allowed = new Set(["http://localhost:3002"]);

describe("isTrustedSender", () => {
  it("trusts only the main frame of an allowed origin", () => {
    expect(isTrustedSender({ frameUrl: "http://localhost:3002/chat?x=1", isMainFrame: true }, allowed)).toEqual({ trusted: true });
  });

  it("refuses when the frame is gone", () => {
    expect(isTrustedSender({ frameUrl: null, isMainFrame: true }, allowed)).toEqual({ trusted: false, reason: "no-frame" });
    expect(isTrustedSender({ frameUrl: undefined, isMainFrame: true }, allowed)).toEqual({ trusted: false, reason: "no-frame" });
    expect(isTrustedSender({ frameUrl: "", isMainFrame: true }, allowed)).toEqual({ trusted: false, reason: "no-frame" });
  });

  it("refuses sub-frames even from an allowed origin (artifact iframes)", () => {
    expect(isTrustedSender({ frameUrl: "http://localhost:3002/artifact", isMainFrame: false }, allowed)).toEqual({ trusted: false, reason: "sub-frame" });
    expect(isTrustedSender({ frameUrl: "about:srcdoc", isMainFrame: false }, allowed)).toEqual({ trusted: false, reason: "sub-frame" });
  });

  it("refuses the prefix trick and the userinfo trick", () => {
    expect(isTrustedSender({ frameUrl: "https://evil.com/?x=http://localhost:3002", isMainFrame: true }, allowed)).toEqual({ trusted: false, reason: "origin-not-allowed" });
    expect(isTrustedSender({ frameUrl: "http://localhost:3002@evil.com/", isMainFrame: true }, allowed)).toEqual({ trusted: false, reason: "origin-not-allowed" });
  });

  it("refuses non-http frames and garbage", () => {
    expect(isTrustedSender({ frameUrl: "file:///C:/x.html", isMainFrame: true }, allowed)).toEqual({ trusted: false, reason: "origin-not-allowed" });
    expect(isTrustedSender({ frameUrl: "about:blank", isMainFrame: true }, allowed)).toEqual({ trusted: false, reason: "origin-not-allowed" });
    expect(isTrustedSender({ frameUrl: "nope", isMainFrame: true }, allowed)).toEqual({ trusted: false, reason: "unparseable" });
  });

  it("refuses scheme/port/host variations of an allowed origin", () => {
    for (const url of ["https://localhost:3002/", "http://localhost:3003/", "http://127.0.0.1:3002/", "http://localhost.evil.com:3002/"]) {
      expect(isTrustedSender({ frameUrl: url, isMainFrame: true }, allowed)).toMatchObject({ trusted: false });
    }
  });
});
