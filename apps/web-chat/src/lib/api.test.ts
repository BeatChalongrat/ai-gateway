// ApiClient auth-retry tests — pins the single-flight 401 → refresh →
// retry contract: N concurrent 401s trigger exactly ONE refresh, the
// original requests replay once, and a failed refresh bounces to the
// Portal login. fetch and the auth module are mocked.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockRefreshSession, mockRedirect } = vi.hoisted(() => ({
  mockRefreshSession: vi.fn(),
  mockRedirect: vi.fn(),
}));

vi.mock("./auth", () => ({
  refreshSession: mockRefreshSession,
  redirectToPortalLogin: mockRedirect,
  // bridge.ts (imported transitively via api.ts) reads this; keep the plain
  // browser transport in these tests.
  HOSTED_MODE: false,
}));

import { api, ApiError, sharedRefresh } from "./api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiClient 401 → refresh → retry", () => {
  it("retries the original request once after a successful refresh", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: "unauthorized" } }))
      .mockResolvedValueOnce(jsonResponse(200, { models: [] }));
    mockRefreshSession.mockResolvedValue(true);

    const res = await api.models();

    expect(res).toEqual({ models: [] });
    expect(mockRefreshSession).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // Same URL + credentials on the replay.
    expect(fetchMock.mock.calls[0][0]).toBe(fetchMock.mock.calls[1][0]);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ credentials: "include" });
    expect(mockRedirect).not.toHaveBeenCalled();
  });

  it("coalesces concurrent 401s into exactly ONE refresh (single-flight)", async () => {
    let refreshed = false;
    let releaseRefresh!: (ok: boolean) => void;
    mockRefreshSession.mockImplementation(
      () =>
        new Promise<boolean>((resolve) => {
          releaseRefresh = (ok) => {
            refreshed = ok;
            resolve(ok);
          };
        }),
    );
    fetchMock.mockImplementation(async () =>
      refreshed
        ? jsonResponse(200, { models: [] })
        : jsonResponse(401, { error: { code: "unauthorized" } }),
    );

    const inFlight = Promise.all([api.models(), api.models(), api.models()]);
    // Let all three hit the 401 and pile onto sharedRefresh.
    await new Promise((r) => setTimeout(r, 0));
    releaseRefresh(true);
    const results = await inFlight;

    expect(results).toEqual([{ models: [] }, { models: [] }, { models: [] }]);
    expect(mockRefreshSession).toHaveBeenCalledTimes(1);
    // 3 original + 3 replays.
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it("redirects to the Portal login when refresh fails, and surfaces the 401", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { error: { code: "unauthorized", message: "expired" } }));
    mockRefreshSession.mockResolvedValue(false);

    await expect(api.models()).rejects.toMatchObject({
      status: 401,
      code: "unauthorized",
    });
    expect(mockRefreshSession).toHaveBeenCalledTimes(1);
    expect(mockRedirect).toHaveBeenCalledTimes(1);
    // No replay after a failed refresh.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not refresh on a non-401 error; throws a typed ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(500, { error: { code: "internal_error", message: "boom" } }),
    );

    const err = await api.models().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 500, code: "internal_error", message: "boom" });
    expect(mockRefreshSession).not.toHaveBeenCalled();
    expect(mockRedirect).not.toHaveBeenCalled();
  });

  it("a retried request that 401s again is NOT retried a second time (no loop)", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { error: { code: "unauthorized" } }));
    mockRefreshSession.mockResolvedValue(true);

    await expect(api.models()).rejects.toMatchObject({ status: 401 });
    // original + exactly one replay.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(mockRefreshSession).toHaveBeenCalledTimes(1);
  });

  it("sharedRefresh resets after settling so a later 401 can refresh again", async () => {
    mockRefreshSession.mockResolvedValue(true);
    await sharedRefresh();
    await sharedRefresh();
    expect(mockRefreshSession).toHaveBeenCalledTimes(2);
  });
});
