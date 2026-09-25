// FIX130 E2 — the Portal middleware checks the session for real.
//
// Before: "a portal cookie is present" = signed in. A device holding a
// REVOKED token was redirected from /login back to the ?redirect= target
// (Web Chat), which found the token dead and sent it back to /login — a
// loop. Now the backend is asked (GET /portal/me, then POST
// /portal/auth/refresh), with "unreachable" kept apart from "rejected".

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { middleware } from "./middleware";
import { checkSession, setCookiesOf } from "./lib/session-check";

const ORIGIN = "http://localhost:3001";
const CHAT = "http://localhost:3002/chat";

type Route = (url: string, init: RequestInit) => Response | Promise<Response>;
let fetchMock: ReturnType<typeof vi.fn>;

function backend(routes: { me?: Route; refresh?: Route }) {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if (url.endsWith("/portal/me") && routes.me) return routes.me(url, init);
    if (url.endsWith("/portal/auth/refresh") && routes.refresh) return routes.refresh(url, init);
    throw new Error(`unexpected backend call ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function json(status: number, body: unknown, setCookies: string[] = []): Response {
  const headers = new Headers({ "content-type": "application/json" });
  for (const c of setCookies) headers.append("set-cookie", c);
  return new Response(JSON.stringify(body), { status, headers });
}

const CLEAR = [
  'portal_access_token=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=lax',
  'portal_refresh_token=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=lax',
];
const ROTATED = [
  "portal_access_token=NEW-ACCESS; HttpOnly; Max-Age=3600; Path=/; SameSite=lax",
  "portal_refresh_token=NEW-REFRESH; HttpOnly; Max-Age=7776000; Path=/; SameSite=lax",
];

function req(path: string, cookies: Record<string, string> = {}): NextRequest {
  const r = new NextRequest(new URL(path, ORIGIN));
  for (const [k, v] of Object.entries(cookies)) r.cookies.set(k, v);
  return r;
}

const isNext = (res: Response) => res.headers.get("x-middleware-next") === "1";
const location = (res: Response) => res.headers.get("location");
const setCookies = (res: Response) => setCookiesOf(res);

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("revoked token (signed out elsewhere)", () => {
  beforeEach(() =>
    backend({
      me: () => json(401, { error: { code: "token_revoked" } }),
      refresh: () => json(401, { error: { code: "refresh_reuse" } }, CLEAR),
    }),
  );

  it("renders the login page instead of redirecting back to Web Chat (no loop)", async () => {
    const res = await middleware(
      req(`/login?redirect=${encodeURIComponent(CHAT)}`, {
        portal_access_token: "REVOKED",
        portal_refresh_token: "DEAD",
      }),
    );
    expect(location(res)).toBeNull();
    expect(isNext(res)).toBe(true);
    // The stale cookies are expired on this response, so the next load is clean.
    const cleared = setCookies(res).join("\n");
    expect(cleared).toMatch(/portal_access_token=[^;]*;.*Max-Age=0/i);
    expect(cleared).toMatch(/portal_refresh_token=[^;]*;.*Max-Age=0/i);
  });

  it("an app page goes to /login once, with the cookies cleared — and /login then renders", async () => {
    const res = await middleware(
      req("/dashboard", { portal_access_token: "REVOKED", portal_refresh_token: "DEAD" }),
    );
    expect(location(res)).toBe(`${ORIGIN}/login?redirect=%2Fdashboard`);
    expect(setCookies(res).join("\n")).toMatch(/portal_access_token=[^;]*;.*Max-Age=0/i);
    // The browser follows with no portal cookie: the login page renders.
    const next = await middleware(req("/login?redirect=%2Fdashboard"));
    expect(isNext(next)).toBe(true);
    expect(location(next)).toBeNull();
  });

  it("only the portal cookies are sent to the backend", async () => {
    await middleware(
      req("/login", {
        portal_access_token: "REVOKED",
        portal_refresh_token: "DEAD",
        "next-auth.session-token": "NEXTAUTH-SECRET",
      }),
    );
    const cookieHeaders = fetchMock.mock.calls.map(([, init]) => new Headers(init?.headers).get("cookie") ?? "");
    expect(cookieHeaders.join("|")).not.toContain("NEXTAUTH-SECRET");
    expect(cookieHeaders[0]).toBe("portal_access_token=REVOKED");
  });
});

describe("valid session — same behaviour as before", () => {
  beforeEach(() =>
    backend({
      me: () => json(200, { id: "u1", email: "u@example.com" }),
      refresh: () => {
        throw new Error("a valid access token must not rotate the session");
      },
    }),
  );

  it("/login?redirect=<Web Chat> goes to Web Chat", async () => {
    const res = await middleware(
      req(`/login?redirect=${encodeURIComponent(CHAT)}`, {
        portal_access_token: "OK",
        portal_refresh_token: "OK-R",
      }),
    );
    expect(location(res)).toBe(CHAT);
    expect(setCookies(res)).toEqual([]);
  });

  it("/login without a redirect goes to /dashboard; an open redirect is dropped", async () => {
    const plain = await middleware(req("/login", { portal_access_token: "OK" }));
    expect(location(plain)).toBe(`${ORIGIN}/dashboard`);
    const evil = await middleware(req("/login?redirect=https://evil.example/x", { portal_access_token: "OK" }));
    expect(location(evil)).toBe(`${ORIGIN}/dashboard`);
  });

  it("an app page renders", async () => {
    const res = await middleware(req("/projects/abc", { portal_access_token: "OK" }));
    expect(isNext(res)).toBe(true);
    expect(setCookies(res)).toEqual([]);
  });

  it("public pages and cookie-less requests never call the backend", async () => {
    expect(isNext(await middleware(req("/pricing", { portal_access_token: "OK" })))).toBe(true);
    const anon = await middleware(req("/settings?tab=keys"));
    expect(location(anon)).toBe(`${ORIGIN}/login?redirect=%2Fsettings%3Ftab%3Dkeys`);
    expect(isNext(await middleware(req("/signup")))).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("expired access token + valid refresh token", () => {
  beforeEach(() =>
    backend({
      me: () => json(401, { error: { code: "authentication_error", message: "Token has expired" } }),
      refresh: () => json(200, { user: { id: "u1" } }, ROTATED),
    }),
  );

  it("counts as signed in: the app page renders and the rotated cookies ride the response", async () => {
    const res = await middleware(
      req("/dashboard", { portal_access_token: "EXPIRED", portal_refresh_token: "GOOD" }),
    );
    expect(isNext(res)).toBe(true);
    expect(location(res)).toBeNull();
    expect(setCookies(res)).toEqual(ROTATED);
    const refreshCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/portal/auth/refresh"));
    expect(refreshCall?.[1]?.method).toBe("POST");
    expect(new Headers(refreshCall?.[1]?.headers).get("cookie")).toContain("portal_refresh_token=GOOD");
  });

  it("the browser already dropped the access cookie (Max-Age passed): refresh alone decides", async () => {
    const res = await middleware(
      req(`/login?redirect=${encodeURIComponent(CHAT)}`, { portal_refresh_token: "GOOD" }),
    );
    expect(location(res)).toBe(CHAT);
    expect(setCookies(res)).toEqual(ROTATED);
    expect(fetchMock).toHaveBeenCalledTimes(1); // no /portal/me without an access cookie
  });
});

describe("backend unreachable — never a lock-out, never a loop", () => {
  for (const [name, route] of [
    ["network error", () => Promise.reject(new TypeError("fetch failed"))],
    ["503 during a redeploy", () => json(503, { error: { code: "unavailable" } })],
  ] as const) {
    it(`${name}: /login renders (no redirect), an app page renders, nothing is cleared`, async () => {
      backend({ me: route, refresh: route });
      const login = await middleware(
        req(`/login?redirect=${encodeURIComponent(CHAT)}`, {
          portal_access_token: "WHATEVER",
          portal_refresh_token: "WHATEVER-R",
        }),
      );
      expect(isNext(login)).toBe(true);
      expect(location(login)).toBeNull();
      expect(setCookies(login)).toEqual([]);
      const app = await middleware(req("/usage", { portal_access_token: "WHATEVER" }));
      expect(isNext(app)).toBe(true);
      expect(setCookies(app)).toEqual([]);
    });
  }

  it("a backend that hangs is given up on after the timeout (unknown)", async () => {
    const hang = vi.fn(
      (_u: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_, reject) =>
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))),
        ),
    );
    const t0 = Date.now();
    const v = await checkSession("A", "R", { fetchImpl: hang as unknown as typeof fetch, timeoutMs: 50 });
    expect(v).toEqual({ state: "unknown", setCookies: [], reason: "unreachable" });
    expect(Date.now() - t0).toBeLessThan(1000);
  });
});

describe("setCookiesOf", () => {
  it("keeps cookies with an Expires date whole when only the joined header exists", () => {
    const joined = {
      headers: {
        get: () => 'a=1; expires=Wed, 23 Sep 2026 10:00:00 GMT; Path=/, b=2; Max-Age=0; Path=/',
      },
    } as unknown as Response;
    expect(setCookiesOf(joined)).toEqual([
      "a=1; expires=Wed, 23 Sep 2026 10:00:00 GMT; Path=/",
      "b=2; Max-Age=0; Path=/",
    ]);
  });
});

// FIX134 C1 — the liveness route the Caddy front door checks is public:
// the middleware answers it without asking the backend, so a slow or down
// backend never turns a health probe into a redirect (or a timeout).
describe("liveness (/healthz)", () => {
  beforeEach(() =>
    backend({
      me: () => {
        throw new Error("/healthz must not check the session");
      },
      refresh: () => {
        throw new Error("/healthz must not check the session");
      },
    }),
  );

  it("passes through with no cookie and no backend call", async () => {
    const res = await middleware(req("/healthz"));
    expect(location(res)).toBeNull();
    expect(isNext(res)).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
