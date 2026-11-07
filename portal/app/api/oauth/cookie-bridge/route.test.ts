// Phase 11 E3 — OAuth tokens must never reach client-side JS.
//
// Pins the two halves of the fix:
//   1. The NextAuth ``session`` callback no longer copies the portal
//      token pair onto the client-readable session object (any script
//      could read them via GET /api/auth/session).
//   2. The server-side cookie-bridge route reads the tokens from the
//      HttpOnly NextAuth JWT (getToken), exchanges them with the
//      backend, and relays only Set-Cookie headers — the tokens appear
//      in neither the request nor the response body.

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockGetToken } = vi.hoisted(() => ({ mockGetToken: vi.fn() }));

vi.mock("next-auth/jwt", () => ({ getToken: mockGetToken }));

import { authOptions, type PortalTokens } from "../../auth/[...nextauth]/authOptions";
import { POST } from "./route";

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function bridgeRequest(): NextRequest {
  return new NextRequest("http://localhost:3001/api/oauth/cookie-bridge", {
    method: "POST",
  });
}

describe("NextAuth session callback (E3)", () => {
  it("does NOT expose portal tokens on the client-readable session", async () => {
    const session = { user: { email: "b@example.com" }, expires: "2026-12-31" };
    const token = {
      portal_access_token: "SECRET-ACCESS",
      portal_refresh_token: "SECRET-REFRESH",
    };

    const result = (await authOptions.callbacks!.session!({
      session,
      token,
    } as never)) as PortalTokens;

    expect(result.portal_access_token).toBeUndefined();
    expect(result.portal_refresh_token).toBeUndefined();
    expect(JSON.stringify(result)).not.toContain("SECRET-ACCESS");
    expect(JSON.stringify(result)).not.toContain("SECRET-REFRESH");
  });

  it("jwt callback still stashes the tokens in the (HttpOnly, encrypted) JWT for the bridge", async () => {
    const token = {};
    const user = {
      id: "u1",
      portal_access_token: "AT",
      portal_refresh_token: "RT",
    };

    const result = (await authOptions.callbacks!.jwt!({
      token,
      user,
    } as never)) as PortalTokens;

    expect(result.portal_access_token).toBe("AT");
    expect(result.portal_refresh_token).toBe("RT");
  });
});

describe("POST /api/oauth/cookie-bridge (E3)", () => {
  it("401s without calling the backend when there is no OAuth JWT", async () => {
    mockGetToken.mockResolvedValue(null);

    const res = await POST(bridgeRequest());

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
    const body = (await res.json()) as { error: { code: string } };
    expect(body.error.code).toBe("no_oauth_session");
  });

  it("exchanges the JWT-held tokens server-side and relays Set-Cookie — tokens never in the response body", async () => {
    mockGetToken.mockResolvedValue({
      portal_access_token: "SECRET-ACCESS",
      portal_refresh_token: "SECRET-REFRESH",
    });
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: [
          ["set-cookie", "portal_access_token=SECRET-ACCESS; HttpOnly; Path=/"],
          ["set-cookie", "portal_refresh_token=SECRET-REFRESH; HttpOnly; Path=/"],
        ],
      }),
    );

    const res = await POST(bridgeRequest());

    // Backend cookie-exchange called server-side with the tokens.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://localhost:8080/portal/auth/cookie-exchange");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      access_token: "SECRET-ACCESS",
      refresh_token: "SECRET-REFRESH",
    });

    // Both HttpOnly cookies relayed onto the browser response.
    expect(res.status).toBe(200);
    const setCookies = res.headers.getSetCookie();
    expect(setCookies).toHaveLength(2);
    expect(setCookies[0]).toContain("portal_access_token=");
    expect(setCookies[1]).toContain("portal_refresh_token=");

    // The response BODY carries no tokens for JS to read.
    const text = await res.text();
    expect(text).not.toContain("SECRET-ACCESS");
    expect(text).not.toContain("SECRET-REFRESH");
  });

  it("relays a backend rejection status without minting cookies", async () => {
    mockGetToken.mockResolvedValue({
      portal_access_token: "forged",
      portal_refresh_token: "forged",
    });
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "invalid_tokens" } }), {
        status: 401,
      }),
    );

    const res = await POST(bridgeRequest());
    expect(res.status).toBe(401);
  });

  it("502s when the backend is unreachable", async () => {
    mockGetToken.mockResolvedValue({
      portal_access_token: "AT",
      portal_refresh_token: "RT",
    });
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED"));

    const res = await POST(bridgeRequest());
    expect(res.status).toBe(502);
  });
});
