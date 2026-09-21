// FIX125 M6 — the silent-refresh tick. A 502 while the gateway redeploys
// must keep the page (and retry on the next focus); only a 401/403 from
// the refresh endpoint sends the person to the login page.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockRedirect } = vi.hoisted(() => ({ mockRedirect: vi.fn() }));

vi.mock("./auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("./auth")>();
  return { ...real, HOSTED_MODE: false, redirectToPortalLogin: mockRedirect };
});

import { startSilentRefresh, stopSilentRefresh } from "./auth-refresh";

const FIFTY_MIN = 50 * 60 * 1000;

describe("FIX125 M6 silent refresh", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    vi.useFakeTimers();
    mockRedirect.mockReset();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    stopSilentRefresh();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("a 502 on the refresh tick does not redirect, and focus retries", async () => {
    fetchMock.mockResolvedValue(new Response("bad gateway", { status: 502 }));
    startSilentRefresh();
    await vi.advanceTimersByTimeAsync(FIFTY_MIN);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(mockRedirect).not.toHaveBeenCalled();

    // Focus retries (lastRefresh was not advanced by the failed tick).
    fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(mockRedirect).not.toHaveBeenCalled();
  });

  it("a network error on the refresh tick does not redirect", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    startSilentRefresh();
    await vi.advanceTimersByTimeAsync(FIFTY_MIN);
    expect(mockRedirect).not.toHaveBeenCalled();
  });

  it("a 401 on the refresh tick redirects to the login", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 401 }));
    startSilentRefresh();
    await vi.advanceTimersByTimeAsync(FIFTY_MIN);
    expect(mockRedirect).toHaveBeenCalledTimes(1);
  });
});
