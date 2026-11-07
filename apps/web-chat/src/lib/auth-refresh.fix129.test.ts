// FIX129 B2 — the session heartbeat. Sign-out revokes the access token on
// the gateway and chat-edge; a tab that is merely open must find out within
// seconds, not at its next click or the 50-minute refresh.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockRedirect } = vi.hoisted(() => ({ mockRedirect: vi.fn() }));

vi.mock("./auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("./auth")>();
  return { ...real, HOSTED_MODE: false, redirectToPortalLogin: mockRedirect };
});

import { startSilentRefresh, stopSilentRefresh } from "./auth-refresh";

const sessionCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/auth/session"));

function respond(fetchMock: ReturnType<typeof vi.fn>, session: number, refresh: number) {
  fetchMock.mockImplementation((url: string) => {
    if (String(url).endsWith("/auth/session")) return Promise.resolve(new Response(null, { status: session }));
    if (String(url).includes("/portal/auth/refresh")) return Promise.resolve(new Response(null, { status: refresh }));
    return Promise.resolve(new Response(null, { status: 200 }));
  });
}

describe("FIX129 B2 session heartbeat", () => {
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

  it("asks chat-edge every 5 s while the tab is visible", async () => {
    respond(fetchMock, 204, 200);
    startSilentRefresh();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(sessionCalls(fetchMock)).toHaveLength(3);
    expect(sessionCalls(fetchMock)[0][1]).toMatchObject({ credentials: "include" });
    expect(mockRedirect).not.toHaveBeenCalled();
  });

  it("a revoked session (401) whose refresh is rejected leaves within 5 s", async () => {
    respond(fetchMock, 401, 401);
    startSilentRefresh();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(mockRedirect).toHaveBeenCalledTimes(1);
    // The heartbeat stops with the redirect — no further polling.
    await vi.advanceTimersByTimeAsync(20_000);
    expect(sessionCalls(fetchMock)).toHaveLength(1);
  });

  it("a lapsed access token whose refresh succeeds stays on the page", async () => {
    respond(fetchMock, 401, 200);
    startSilentRefresh();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(mockRedirect).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/portal/auth/refresh"))).toBe(true);
  });

  it("an unreachable chat-edge is not a sign-out", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    startSilentRefresh();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(mockRedirect).not.toHaveBeenCalled();
  });

  it("a hidden tab does not poll", async () => {
    respond(fetchMock, 204, 200);
    const spy = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    try {
      startSilentRefresh();
      await vi.advanceTimersByTimeAsync(15_000);
      expect(sessionCalls(fetchMock)).toHaveLength(0);
    } finally {
      spy.mockRestore();
    }
  });
});
