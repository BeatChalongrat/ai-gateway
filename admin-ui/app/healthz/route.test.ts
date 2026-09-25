// FIX134 C1 — the liveness route the Caddy front door checks.
//
// Before: the Caddyfile probed /login (a full page render) with a 3 s
// timeout, so a busy box (deploys, the 03:15 backup) logged "context
// deadline exceeded" and took admin-ui out of rotation. /healthz answers
// from the Node process alone: no auth, no database, no fetch — and the
// middleware lets it through without the admin_session cookie.

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { middleware } from "../../middleware";
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
  it("answers 200 {ok, service: admin-ui} without a network call", async () => {
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, service: "admin-ui" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is rendered per request, never at build time", () => {
    expect(dynamic).toBe("force-dynamic");
  });
});

describe("middleware and /healthz", () => {
  it("passes /healthz through without the admin_session cookie", () => {
    const res = middleware(new NextRequest("http://localhost:3000/healthz"));
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });

  it("still sends an app page without the cookie to /login", () => {
    const res = middleware(new NextRequest("http://localhost:3000/models"));
    expect(res.headers.get("location")).toBe("http://localhost:3000/login");
  });
});
