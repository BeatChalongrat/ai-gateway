// Portal api client tests — pins the 401 → single-flight refresh →
// retry contract, the refresh-failure logout punt, and the error
// envelope parsing. fetch is fully mocked; everything routes through
// the same-origin ``/api`` proxy prefix.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, PortalApiError } from "./api";

function jsonResponse(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

/** Route-style fetch mock: refresh endpoint + a default handler. */
function installFetch(opts: {
  refresh: () => Response | Promise<Response>;
  other: (url: string, init?: RequestInit) => Response | Promise<Response>;
}) {
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/portal/auth/refresh") return opts.refresh();
    return opts.other(url, init);
  });
  vi.stubGlobal("fetch", fetchMock);
}

const refreshCalls = () =>
  fetchMock.mock.calls.filter(([u]) => u === "/api/portal/auth/refresh").length;

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("portal api — 401 → refresh → retry", () => {
  it("refreshes once and replays the original request", async () => {
    let refreshed = false;
    installFetch({
      refresh: () => {
        refreshed = true;
        return jsonResponse(200, {});
      },
      other: () =>
        refreshed
          ? jsonResponse(200, { ok: true })
          : jsonResponse(401, { error: { code: "unauthorized", message: "expired" } }),
    });

    const res = await api.get<{ ok: boolean }>("/portal/me");

    expect(res).toEqual({ ok: true });
    expect(refreshCalls()).toBe(1);
    // 401 + refresh + replay = 3 fetches total.
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // Cookie-borne auth on every call.
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
  });

  it("coalesces concurrent 401s into exactly ONE refresh (single-flight)", async () => {
    let refreshed = false;
    let releaseRefresh!: () => void;
    const gate = new Promise<void>((r) => {
      releaseRefresh = r;
    });
    installFetch({
      refresh: async () => {
        await gate;
        refreshed = true;
        return jsonResponse(200, {});
      },
      other: () =>
        refreshed
          ? jsonResponse(200, { ok: true })
          : jsonResponse(401, { error: { code: "unauthorized", message: "x" } }),
    });

    const inFlight = Promise.all([
      api.get("/portal/me"),
      api.get("/portal/usage"),
      api.get("/portal/projects"),
    ]);
    await new Promise((r) => setTimeout(r, 0));
    releaseRefresh();
    const results = await inFlight;

    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(refreshCalls()).toBe(1);
    // 3 originals + 1 refresh + 3 replays.
    expect(fetchMock).toHaveBeenCalledTimes(7);
  });

  it("failed refresh punts to /login and throws session_expired", async () => {
    installFetch({
      refresh: () => jsonResponse(401),
      other: () => jsonResponse(401, { error: { code: "unauthorized", message: "x" } }),
    });
    // jsdom's window.location can't navigate — swap in a plain stub so
    // the redirect target is observable.
    const loc = { href: "/dashboard" };
    const original = window.location;
    Object.defineProperty(window, "location", {
      value: loc,
      writable: true,
      configurable: true,
    });

    try {
      const err = await api.get("/portal/me").catch((e) => e);
      expect(err).toBeInstanceOf(PortalApiError);
      expect(err).toMatchObject({ status: 401, code: "session_expired" });
      expect(loc.href).toBe("/login");
      // one original + one refresh, NO replay.
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      Object.defineProperty(window, "location", {
        value: original,
        writable: true,
        configurable: true,
      });
    }
  });

  it("a replay that 401s again is not retried a second time (no loop)", async () => {
    installFetch({
      refresh: () => jsonResponse(200, {}),
      other: () => jsonResponse(401, { error: { code: "unauthorized", message: "x" } }),
    });

    const err: unknown = await api.get("/portal/me").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(PortalApiError);
    expect((err as PortalApiError).status).toBe(401);
    // original + refresh + one replay — then give up.
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(refreshCalls()).toBe(1);
  });

  it("does not attempt refresh when auth:false", async () => {
    installFetch({
      refresh: () => jsonResponse(200, {}),
      other: () => jsonResponse(401, { error: { code: "unauthorized", message: "x" } }),
    });

    await expect(api.get("/public/thing", { auth: false })).rejects.toMatchObject({
      status: 401,
    });
    expect(refreshCalls()).toBe(0);
  });
});

describe("portal api — envelope + verb plumbing", () => {
  it("parses the backend error envelope into PortalApiError fields", async () => {
    installFetch({
      refresh: () => jsonResponse(200, {}),
      other: () =>
        jsonResponse(422, {
          error: {
            code: "validation_error",
            message: "Bad payload",
            details: { field: "email" },
          },
        }),
    });

    const err = await api.post("/portal/projects", { name: "" }).catch((e) => e);
    expect(err).toBeInstanceOf(PortalApiError);
    expect(err).toMatchObject({
      status: 422,
      code: "validation_error",
      message: "Bad payload",
      details: { field: "email" },
    });
  });

  it("returns undefined for 204 and serializes JSON bodies with the method", async () => {
    installFetch({
      refresh: () => jsonResponse(200, {}),
      other: () => jsonResponse(204),
    });

    const res = await api.patch("/portal/me", { name: "Beat" });
    expect(res).toBeUndefined();
    const [, init] = fetchMock.mock.calls[0];
    expect(init).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({ name: "Beat" }),
      credentials: "include",
      cache: "no-store",
    });
  });

  it("upload() does not set Content-Type and retries once after refresh", async () => {
    let refreshed = false;
    installFetch({
      refresh: () => {
        refreshed = true;
        return jsonResponse(200, {});
      },
      other: () =>
        refreshed
          ? jsonResponse(200, { uploaded: true })
          : jsonResponse(401, { error: { code: "unauthorized", message: "x" } }),
    });

    const form = new FormData();
    const res = await api.upload<{ uploaded: boolean }>("/portal/files", form);
    expect(res).toEqual({ uploaded: true });
    expect(refreshCalls()).toBe(1);
    const [, init] = fetchMock.mock.calls[0];
    // Browser must own the multipart boundary.
    expect(init?.headers).toBeUndefined();
    expect(init?.body).toBe(form);
  });
});
