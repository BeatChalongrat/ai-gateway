// FIX130 C3.1 — the page-load session check can neither hang nor sign the
// device out because the backend is slow or down.
//
// NIGHT2 F1: /portal/me was held 12–20 s, then 502'd, then never answered;
// the app was a blank spinner with an empty DOM at 90 s. And since FIX129 a
// failed check bounced through redirectToPortalLogin, which POSTs the
// gateway logout (revokes the tokens): a wedged backend signed people out.
//
// Driven through the auth store (what every route layout calls). The only
// network is a stubbed fetch; the rejected/unreachable verdict is read from
// the store and from which URLs were hit.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./auth", async (importOriginal) => ({ ...(await importOriginal<object>()), HOSTED_MODE: false }));

import { useAuthStore } from "@/stores/authStore";

const initial = useAuthStore.getState();
let fetchMock: ReturnType<typeof vi.fn>;
const hits = (part: string) => fetchMock.mock.calls.filter((c) => String(c[0]).includes(part)).length;
const me = { id: "u1", email: "a@b.c", tenant_id: "t1", tenant_role: "owner" };

/** fetch that honours the request's AbortSignal the way the browser does. */
function hang(_url: string, init?: RequestInit): Promise<Response> {
  return new Promise((_, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  });
}

beforeEach(() => {
  useAuthStore.setState({ ...initial, user: null, ready: false }, true);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("FIX130 C3.1 — /portal/me that never answers", () => {
  it("settles (ready, no user) within the 8 s deadline instead of spinning forever", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(hang);
    let settled = false;
    void useAuthStore.getState().checkAuth().then(() => { settled = true; });
    await vi.advanceTimersByTimeAsync(7_000);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(settled).toBe(true);
    expect(useAuthStore.getState().ready).toBe(true);
    expect(useAuthStore.getState().user).toBeNull();
    expect((useAuthStore.getState() as { status?: string }).status).toBe("unreachable");
    expect(hits("/portal/auth/logout")).toBe(0);
  });
});

describe("FIX130 C3.1 — rejected vs unreachable", () => {
  it("502 is unreachable, not a sign-out", async () => {
    fetchMock.mockResolvedValue(new Response("bad gateway", { status: 502 }));
    await useAuthStore.getState().checkAuth();
    expect((useAuthStore.getState() as { status?: string }).status).toBe("unreachable");
    expect(hits("/portal/auth/logout")).toBe(0);
  });

  it("a network error is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await useAuthStore.getState().checkAuth();
    expect((useAuthStore.getState() as { status?: string }).status).toBe("unreachable");
  });

  it("401 whose refresh is refused too is rejected", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      String(url).includes("/portal/auth/refresh") ? new Response(null, { status: 401 }) : new Response(null, { status: 401 }),
    );
    await useAuthStore.getState().checkAuth();
    expect((useAuthStore.getState() as { status?: string }).status).toBe("rejected");
    expect(useAuthStore.getState().user).toBeNull();
  });

  it("401 on an expired access cookie with a good refresh cookie: refresh, then the person — not a login bounce", async () => {
    let meCalls = 0;
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes("/portal/auth/refresh")) return new Response(null, { status: 200 });
      meCalls += 1;
      return meCalls === 1 ? new Response(null, { status: 401 }) : new Response(JSON.stringify(me), { status: 200 });
    });
    await useAuthStore.getState().checkAuth();
    expect(useAuthStore.getState().user?.user_id).toBe("u1");
    expect((useAuthStore.getState() as { status?: string }).status).toBe("ok");
  });

  it("two checks at mount (AppProviders + the layout) share one request", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(me), { status: 200 }));
    await Promise.all([useAuthStore.getState().checkAuth(), useAuthStore.getState().checkAuth()]);
    expect(hits("/portal/me")).toBe(1);
    expect(useAuthStore.getState().user?.user_id).toBe("u1");
  });
});
