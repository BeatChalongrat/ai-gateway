// FIX134 C1 — the liveness route the Caddy front door checks.
//
// Before: the Caddyfile probed /login (a full page render, plus the
// middleware's session check against the backend) with a 3 s timeout, so a
// busy box (deploys, the 03:15 backup) logged "context deadline exceeded"
// and took the portal out of rotation. /healthz answers from the Node
// process alone: no auth, no database, no fetch.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET, dynamic } from "./route";

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => {
    throw new Error("the liveness route must not call anything");
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GET /healthz", () => {
  it("answers 200 {ok, service: portal} without a network call", async () => {
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, service: "portal" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is rendered per request, never at build time", () => {
    expect(dynamic).toBe("force-dynamic");
  });
});
