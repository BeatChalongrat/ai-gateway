// FIX134 A3 — the main process's own https calls go through Chromium's network
// stack (where the pinned CA applies); every other URL stays on Node's fetch.
import { describe, expect, it, vi } from "vitest";
import { fetchFor, installNodeFetchTrust, type LocalCaTrust } from "../src/main/local-ca";

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
  it("replaces the global fetch, never sends cookies by default, keeps the caller's init", async () => {
    const nodeFetch = vi.fn((_i: unknown, _init?: RequestInit) => ok("node"));
    const netFetch = vi.fn((_i: unknown, _init?: RequestInit) => ok("net"));
    const g = { fetch: nodeFetch as unknown as typeof fetch };
    const lines: string[] = [];
    installNodeFetchTrust(trust, netFetch as never, (l) => lines.push(l), g as never);
    expect(g.fetch).not.toBe(nodeFetch);
    expect(await (await g.fetch("https://localhost:8080/v1/desktop/version", { method: "POST", headers: { a: "b" } })).text()).toBe("net");
    expect(netFetch).toHaveBeenCalledWith("https://localhost:8080/v1/desktop/version", { credentials: "omit", method: "POST", headers: { a: "b" } });
    expect(await (await g.fetch("https://localhost:8080/x", { credentials: "include" })).text()).toBe("net");
    expect(netFetch).toHaveBeenLastCalledWith("https://localhost:8080/x", { credentials: "include" });
    expect(await (await g.fetch("https://api.openai.com/v1/models")).text()).toBe("node");
    expect(nodeFetch).toHaveBeenCalledTimes(1);
    expect(lines[0]).toContain("127.0.0.1, localhost");
  });
});
