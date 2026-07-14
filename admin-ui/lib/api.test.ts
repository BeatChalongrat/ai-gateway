// Admin-UI api client tests — pins the double-submit CSRF contract
// (csrf_token cookie echoed into X-CSRF-Token on mutations only),
// the in-flight GET dedup, and the 401 session-clear path. fetch is
// fully mocked.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./api";

const BASE = "http://localhost:8080";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

function headersOfCall(i: number): Record<string, string> {
  return (fetchMock.mock.calls[i][1] as RequestInit).headers as Record<string, string>;
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn(async () => jsonResponse(200, { ok: true }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  // Evict the CSRF cookie between tests (jsdom shares the document).
  document.cookie = "csrf_token=; Max-Age=0; path=/";
  localStorage.clear();
});

describe("double-submit CSRF header", () => {
  it("echoes the csrf_token cookie into X-CSRF-Token on POST/PUT/PATCH/DELETE", async () => {
    document.cookie = "csrf_token=tok-abc123";

    await api.post("/admin/tenants", { name: "t" });
    await api.put("/admin/tenants/1", { name: "t2" });
    await api.patch("/admin/tenants/1", { name: "t3" });
    await api.delete("/admin/tenants/1");

    for (let i = 0; i < 4; i++) {
      expect(headersOfCall(i)["X-CSRF-Token"]).toBe("tok-abc123");
      // Session cookie rides along on every call.
      expect(fetchMock.mock.calls[i][1]).toMatchObject({ credentials: "include" });
    }
  });

  it("URL-decodes the cookie value before echoing it", async () => {
    document.cookie = `csrf_token=${encodeURIComponent("a+b/c=")}`;
    await api.post("/admin/x", {});
    expect(headersOfCall(0)["X-CSRF-Token"]).toBe("a+b/c=");
  });

  it("does NOT attach X-CSRF-Token on GET", async () => {
    document.cookie = "csrf_token=tok-abc123";
    await api.get("/admin/tenants");
    expect(headersOfCall(0)["X-CSRF-Token"]).toBeUndefined();
  });

  it("omits the header entirely when no csrf_token cookie exists", async () => {
    await api.post("/admin/x", {});
    expect(headersOfCall(0)["X-CSRF-Token"]).toBeUndefined();
  });
});

describe("request plumbing", () => {
  it("dedups concurrent identical GETs into one fetch", async () => {
    let release!: () => void;
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          release = () => resolve(jsonResponse(200, { n: 1 }));
        }),
    );

    const a = api.get("/admin/stats");
    const b = api.get("/admin/stats");
    release();
    expect(await a).toEqual({ n: 1 });
    expect(await b).toEqual({ n: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not dedup sequential GETs (cache is in-flight only)", async () => {
    await api.get("/admin/stats");
    await api.get("/admin/stats");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("throws a typed ApiError carrying the response body on non-2xx", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(422, { error: { message: "bad input" } }),
    );
    const err = await api.post("/admin/x", {}).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.body).toEqual({ error: { message: "bad input" } });
  });

  it("returns null on 204", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    expect(await api.delete("/admin/x")).toBeNull();
  });
});

describe("401 → session clear", () => {
  it("fires the logout endpoint, evicts the cached session, and redirects to /login", async () => {
    // Swap jsdom's location for an observable stub (assign navigates).
    const assign = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      value: { pathname: "/tenants", assign },
      writable: true,
      configurable: true,
    });
    localStorage.setItem("admin_session_cache", JSON.stringify({ role: "admin" }));

    fetchMock.mockImplementation(async (url: string) => {
      if (url === `${BASE}/auth/session/logout`) return jsonResponse(200, {});
      return jsonResponse(401, { error: { message: "unauthenticated" } });
    });

    try {
      const err = await api.get("/admin/tenants").catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(401);

      // clearInvalidSession is fire-and-forget — let it settle.
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));

      const logoutCalls = fetchMock.mock.calls.filter(
        ([u]) => u === `${BASE}/auth/session/logout`,
      );
      expect(logoutCalls).toHaveLength(1);
      expect(localStorage.getItem("admin_session_cache")).toBeNull();
      expect(assign).toHaveBeenCalledWith("/login");
    } finally {
      Object.defineProperty(window, "location", {
        value: originalLocation,
        writable: true,
        configurable: true,
      });
    }
  });
});
