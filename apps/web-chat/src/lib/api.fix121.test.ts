// FIX121 C3 — the transport half: X-Request-Id leaves the page only when the
// server is known to accept it.
//
// A custom header on a cross-origin request is preflighted; a header the
// server's Access-Control-Allow-Headers does not list means the browser never
// sends the POST. A chat-edge older than FIX121 does not list X-Request-Id,
// so a page deployed ahead of it must keep sending what it always sent.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./auth", () => ({ refreshSession: vi.fn(), redirectToPortalLogin: vi.fn(), HOSTED_MODE: false }));

import { api, requestIdAllowed, resetRequestIdProbeForTest } from "./api";

let fetchMock: ReturnType<typeof vi.fn>;
const sse = () => new Response("event: message_complete\ndata: {}\n\n", { status: 200, headers: { "Content-Type": "text/event-stream" } });
const headerOf = (call: number, name: string) => new Headers((fetchMock.mock.calls[call][1] as RequestInit).headers).get(name);

beforeEach(() => {
  resetRequestIdProbeForTest();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX121 — X-Request-Id and the server's CORS allow-list", () => {
  it("a server that accepts the header gets it on the send, and is asked only once", async () => {
    fetchMock.mockImplementation(async (url: string) => (String(url).endsWith("/health") ? new Response("ok", { status: 200 }) : sse()));
    const ctrl = new AbortController();
    await api.openMessageStream("c1", "hi", ctrl.signal, undefined, false, false, null, "11111111-2222-4333-8444-555555555555");
    await api.openMessageStream("c1", "again", ctrl.signal, undefined, false, false, null, "66666666-7777-4888-8999-000000000000");

    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls.filter((u) => u.endsWith("/health"))).toHaveLength(1);
    expect(headerOf(0, "X-Request-Id")).toBe("capability-probe-0001"); // the probe carries the header: that IS the question
    expect(headerOf(1, "X-Request-Id")).toBe("11111111-2222-4333-8444-555555555555");
    expect(headerOf(2, "X-Request-Id")).toBe("66666666-7777-4888-8999-000000000000");
    expect(headerOf(1, "Content-Type")).toBe("application/json");
  });

  it("a server whose preflight refuses the header still gets the send — without it, exactly as before", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/health")) throw new TypeError("Failed to fetch"); // what a refused preflight looks like to a page
      return sse();
    });
    const res = await api.openMessageStream("c1", "hi", new AbortController().signal, undefined, false, false, null, "11111111-2222-4333-8444-555555555555");
    expect(res.ok).toBe(true);
    expect(await requestIdAllowed()).toBe(false);
    const post = fetchMock.mock.calls.findIndex((c) => String(c[0]).endsWith("/conversations/c1/messages"));
    expect(headerOf(post, "X-Request-Id")).toBeNull();
  });

  it("a send with no id asks nothing and sends no header", async () => {
    fetchMock.mockResolvedValue(sse());
    await api.openMessageStream("c1", "hi", new AbortController().signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(headerOf(0, "X-Request-Id")).toBeNull();
  });
});
