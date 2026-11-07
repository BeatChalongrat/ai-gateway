// FIX125 M6 — an idle tab must not be thrown out of its conversation.
//
//  * refreshSession tells "rejected" (401/403: really signed out) from
//    "unreachable" (network error / 5xx during a redeploy: keep the page).
//  * The login bounce returns to the page the person is ON (/chat/<id>),
//    same-origin only, with /chat as the fallback.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PORTAL_URL, portalLoginUrl, portalReturnTarget, refreshSession } from "./auth";

const ORIGIN = "http://localhost:3002";

describe("FIX125 M6 refreshSession outcome", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("200 → ok", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
    expect(await refreshSession()).toBe("ok");
  });

  it("502 during a redeploy → unreachable (not signed out)", async () => {
    fetchMock.mockResolvedValue(new Response("bad gateway", { status: 502 }));
    expect(await refreshSession()).toBe("unreachable");
  });

  it("network error → unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await refreshSession()).toBe("unreachable");
  });

  it("401 and 403 → rejected", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    expect(await refreshSession()).toBe("rejected");
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 403 }));
    expect(await refreshSession()).toBe("rejected");
  });
});

describe("FIX125 M6 login return target", () => {
  it("returns to the current conversation, not /chat", () => {
    const url = portalLoginUrl({ origin: ORIGIN, pathname: "/chat/conv-123", search: "?x=1" });
    expect(url).toBe(`${PORTAL_URL}/login?redirect=${encodeURIComponent(`${ORIGIN}/chat/conv-123?x=1`)}`);
  });

  it("uses the live window location", () => {
    window.history.pushState({}, "", "/chat/abc-def");
    try {
      const url = new URL(portalLoginUrl(window.location));
      expect(url.searchParams.get("redirect")).toBe(`${window.location.origin}/chat/abc-def`);
    } finally {
      window.history.pushState({}, "", "/");
    }
  });

  it("rejects anything that is not a same-origin path (falls back to /chat)", () => {
    const fb = `${ORIGIN}/chat`;
    for (const bad of [
      "https://evil.example/chat/1",
      "//evil.example/chat/1",
      "/\\evil.example/chat",
      "javascript:alert(1)",
      "chat/1",
      "",
      "/chat/\n//evil",
    ]) {
      expect(portalReturnTarget(ORIGIN, bad), bad).toBe(fb);
    }
    expect(portalReturnTarget(ORIGIN, null)).toBe(fb);
    expect(portalReturnTarget(ORIGIN, "/chat/1?a=b")).toBe(`${ORIGIN}/chat/1?a=b`);
  });
});
