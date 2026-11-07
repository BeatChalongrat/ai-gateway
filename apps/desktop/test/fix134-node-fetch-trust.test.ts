// FIX134 A3 — the main process's own https calls go through Chromium's network
// stack (net.request on the default session, where the pinned CA applies);
// every other URL stays on Node's fetch. The transport must hand back
// Set-Cookie: PortalAuthClient.refresh reads the rotated tokens from it (the
// first e58 build used net.fetch, which hides Set-Cookie, and its boot refresh
// came back "200 no_tokens_in_response" and cleared the session — live log,
// 2026-09-25 07:11:54Z).
import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";
import { fetchFor, installNodeFetchTrust, netRequestFetch, type LocalCaTrust, type NetRequestFactory } from "../src/main/local-ca";
import { PortalAuthClient } from "../src/main/portal-auth";

const trust: LocalCaTrust = { caPem: "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n", hosts: new Set(["localhost", "127.0.0.1"]) };
const ok = (tag: string) => Promise.resolve(new Response(tag, { status: 200 }));

describe("FIX134 A3 fetchFor", () => {
  const nodeFetch = vi.fn(() => ok("node"));
  const netFetch = vi.fn(() => ok("net"));
  it("routes https to one of the app's own hosts through Chromium", () => {
    expect(fetchFor("https://localhost:8080/portal/auth/refresh", trust, nodeFetch, netFetch)).toBe(netFetch);
    expect(fetchFor(new URL("https://127.0.0.1:4000/health"), trust, nodeFetch, netFetch)).toBe(netFetch);
    expect(fetchFor(new Request("https://LOCALHOST:8080/v1/models"), trust, nodeFetch, netFetch)).toBe(netFetch);
  });
  it("leaves every other URL on Node's fetch", () => {
    expect(fetchFor("https://accounts.google.com/o/oauth2/token", trust, nodeFetch, netFetch)).toBe(nodeFetch);
    expect(fetchFor("http://localhost:3200/mcp/servers/filesystem", trust, nodeFetch, netFetch)).toBe(nodeFetch); // plain http loopback (MCP)
    expect(fetchFor("not a url", trust, nodeFetch, netFetch)).toBe(nodeFetch);
  });
});

describe("FIX134 A3 installNodeFetchTrust", () => {
  it("replaces the global fetch and passes the caller's request through unchanged", async () => {
    const nodeFetch = vi.fn((_i: unknown, _init?: RequestInit) => ok("node"));
    const netFetch = vi.fn((_i: unknown, _init?: RequestInit) => ok("net"));
    const g = { fetch: nodeFetch as unknown as typeof fetch };
    const lines: string[] = [];
    installNodeFetchTrust(trust, netFetch as never, (l) => lines.push(l), g as never);
    expect(await (await g.fetch("https://localhost:8080/v1/desktop/version", { method: "POST", headers: { a: "b" } })).text()).toBe("net");
    expect(netFetch).toHaveBeenCalledWith("https://localhost:8080/v1/desktop/version", { method: "POST", headers: { a: "b" } });
    expect(await (await g.fetch("https://api.openai.com/v1/models")).text()).toBe("node");
    expect(nodeFetch).toHaveBeenCalledTimes(1);
    expect(lines[0]).toContain("127.0.0.1, localhost");
  });
});

// ── a fake Electron net.request ──────────────────────────────────────────────
interface Sent {
  opts: Parameters<NetRequestFactory>[0];
  headers: Record<string, string>;
  body: Buffer[];
  aborted: boolean;
}
function fakeNet(respond: (sent: Sent, res: EventEmitter & { statusCode: number; statusMessage: string; headers: Record<string, string | string[]> }, req: EventEmitter) => void) {
  const sent: Sent[] = [];
  const factory: NetRequestFactory = (opts) => {
    const s: Sent = { opts, headers: {}, body: [], aborted: false };
    sent.push(s);
    const req = new EventEmitter() as EventEmitter & { setHeader: (k: string, v: string) => void; write: (c: Buffer) => void; end: () => void; abort: () => void };
    req.setHeader = (k, v) => {
      s.headers[k.toLowerCase()] = v;
    };
    req.write = (c) => {
      s.body.push(c);
    };
    req.abort = () => {
      s.aborted = true;
    };
    req.end = () => {
      const res = Object.assign(new EventEmitter(), { statusCode: 200, statusMessage: "OK", headers: {} as Record<string, string | string[]> });
      setTimeout(() => respond(s, res, req), 0);
    };
    return req as never;
  };
  return { factory, sent };
}

describe("FIX134 A3 netRequestFetch", () => {
  it("keeps Set-Cookie, so a refresh through the app's transport yields the rotated tokens", async () => {
    const net = fakeNet((_s, res, req) => {
      res.headers = {
        "content-type": "application/json",
        "set-cookie": ["portal_access_token=acc123; Path=/; HttpOnly; Secure", "portal_refresh_token=ref456; Path=/; HttpOnly; Secure"],
      };
      req.emit("response", res);
      res.emit("data", Buffer.from(JSON.stringify({ user: { email: "smoke@gbtech.co.th" } })));
      res.emit("end");
    });
    const client = new PortalAuthClient("https://localhost:8080", netRequestFetch(net.factory));
    const out = await client.refresh("old-refresh");
    expect(out.ok).toBe(true);
    if (out.ok) {
      expect(out.tokens.accessToken).toBe("acc123");
      expect(out.tokens.refreshToken).toBe("ref456");
    }
    expect(net.sent[0].opts).toEqual({ method: "POST", url: "https://localhost:8080/portal/auth/refresh", redirect: "follow", useSessionCookies: false, credentials: "omit" });
    expect(JSON.parse(Buffer.concat(net.sent[0].body).toString())).toEqual({ refresh_token: "old-refresh" });
  });

  it("streams the body chunk by chunk (SSE turns)", async () => {
    const net = fakeNet((_s, res, req) => {
      res.headers = { "content-type": "text/event-stream" };
      req.emit("response", res);
      let i = 0;
      const tick = setInterval(() => {
        if (i < 3) res.emit("data", Buffer.from(`data: ${i++}\n\n`));
        else {
          clearInterval(tick);
          res.emit("end");
        }
      }, 5);
    });
    const res = await netRequestFetch(net.factory)("https://localhost:8080/v1/chat/completions", { method: "POST", body: "{}" });
    const reader = res.body!.getReader();
    const parts: string[] = [];
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(new TextDecoder().decode(value));
    }
    expect(parts).toEqual(["data: 0\n\n", "data: 1\n\n", "data: 2\n\n"]);
  });

  it("never sends cookies or Chromium-managed headers, forwards the rest", async () => {
    const net = fakeNet((_s, res, req) => {
      req.emit("response", res);
      res.emit("end");
    });
    await netRequestFetch(net.factory)("https://localhost:8080/v1/models", {
      headers: { Authorization: "Bearer t", Cookie: "portal_access_token=x", "Content-Length": "9", "X-Turn-Id": "abc" },
    });
    expect(net.sent[0].headers).toEqual({ authorization: "Bearer t", "x-turn-id": "abc" });
  });

  it("aborts: before the answer rejects, during the body errors the stream; the request is aborted", async () => {
    const hang = fakeNet(() => {
      /* never answers */
    });
    const ctl1 = new AbortController();
    const p = netRequestFetch(hang.factory)("https://localhost:8080/v1/models", { signal: ctl1.signal });
    await new Promise((r) => setTimeout(r, 5)); // the request is out, no answer yet
    ctl1.abort();
    await expect(p).rejects.toThrow(/abort/i);
    expect(hang.sent[0].aborted).toBe(true);

    const slow = fakeNet((_s, res, req) => {
      req.emit("response", res);
      res.emit("data", Buffer.from("data: 0\n\n"));
    });
    const ctl2 = new AbortController();
    const res = await netRequestFetch(slow.factory)("https://localhost:8080/v1/chat/completions", { signal: ctl2.signal });
    const reader = res.body!.getReader();
    await reader.read();
    ctl2.abort();
    await expect(reader.read()).rejects.toThrow(/abort/i);
    expect(slow.sent[0].aborted).toBe(true);
  });

  it("a network error before the answer is a fetch failure, like Node's", async () => {
    const net = fakeNet((_s, _res, req) => req.emit("error", new Error("net::ERR_CONNECTION_REFUSED")));
    await expect(netRequestFetch(net.factory)("https://localhost:8080/health")).rejects.toThrow(/fetch failed/);
  });
});
