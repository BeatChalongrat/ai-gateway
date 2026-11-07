// FIX129 B2 — redirectToPortalLogin expires the stale session cookies through
// the gateway before navigating, so a device whose token was revoked
// elsewhere does not loop between the Portal login and /chat (the Portal's
// middleware treats a present cookie as "signed in").
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("./auth")>();
  return { ...real, HOSTED_MODE: false };
});

import { clearSessionCookies, redirectToPortalLogin } from "./auth";

describe("FIX129 B2 redirectToPortalLogin clears cookies first", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ message: "ok" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs the gateway logout with credentials before the navigation", async () => {
    // Never resolves: the navigation (jsdom cannot) is what waits on it.
    fetchMock.mockReturnValue(new Promise(() => {}));
    redirectToPortalLogin();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/portal\/auth\/logout$/);
    expect(init).toMatchObject({ method: "POST", credentials: "include" });
  });

  it("a failing or slow logout never blocks the bounce for more than 1.5 s", async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockReturnValue(new Promise(() => {}));
      const done = clearSessionCookies();
      await vi.advanceTimersByTimeAsync(1500);
      await expect(done).resolves.toBeUndefined();
      fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
      await expect(clearSessionCookies()).resolves.toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });
});
