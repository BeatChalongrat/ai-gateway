// E2 — the portal auth client's parsing and outcome mapping, with a fake
// fetch. Pins: tokens come from Set-Cookie on refresh and from the BODY on
// ticket exchange; a 200 without tokens is treated as rejected (the
// "200-with-error-envelope" trap); 401 → rejected with the server's code;
// a thrown fetch → network; the logout call carries the refresh cookie and
// nothing else; fingerprints are 8 hex chars and never the input.
//
// LIMITS: no real gateway here. Cookie ATTRIBUTES (HttpOnly/Lax/host-only)
// are decided by the injection code in session-controller.ts against
// Electron's cookie API, which this lane cannot load — the live log lines
// "Jar updated … httpOnly=true sameSite=lax hostOnly=true" are that proof.
import { describe, expect, it } from "vitest";
import { ACCESS_COOKIE, PortalAuthClient, REFRESH_COOKIE, fingerprint, parseSetCookies, tokensFromSetCookies } from "../src/main/portal-auth";

function response(status: number, body: unknown, setCookies: string[] = []): Response {
  const headers = new Headers({ "content-type": "application/json" });
  for (const c of setCookies) headers.append("set-cookie", c);
  return new Response(JSON.stringify(body), { status, headers });
}

describe("parseSetCookies / tokensFromSetCookies", () => {
  it("reads name=value from each header and ignores attributes", () => {
    const m = parseSetCookies([
      `${ACCESS_COOKIE}=AAA; HttpOnly; Path=/; SameSite=lax; Max-Age=3600`,
      `${REFRESH_COOKIE}=BBB; HttpOnly; Path=/; SameSite=lax`,
      "garbage",
      "=novalue",
    ]);
    expect(m.get(ACCESS_COOKIE)).toBe("AAA");
    expect(m.get(REFRESH_COOKIE)).toBe("BBB");
    expect(m.size).toBe(2);
  });

  it("needs both cookies", () => {
    expect(tokensFromSetCookies([`${ACCESS_COOKIE}=A`], "u@x")).toBeNull();
    expect(tokensFromSetCookies([`${ACCESS_COOKIE}=A`, `${REFRESH_COOKIE}=`], "u@x")).toBeNull();
    expect(tokensFromSetCookies([`${ACCESS_COOKIE}=A`, `${REFRESH_COOKIE}=B`], "u@x")).toEqual({ accessToken: "A", refreshToken: "B", userEmail: "u@x" });
  });
});

describe("fingerprint", () => {
  it("is 8 hex chars, stable, and not a fragment of the input", () => {
    const f = fingerprint("secret-token-value");
    expect(f).toMatch(/^[0-9a-f]{8}$/);
    expect(fingerprint("secret-token-value")).toBe(f);
    expect("secret-token-value").not.toContain(f);
    expect(fingerprint("secret-token-valuf")).not.toBe(f);
  });
});

describe("PortalAuthClient", () => {
  it("refresh: tokens from Set-Cookie, email from body", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const client = new PortalAuthClient("http://gw", async (url, init) => {
      calls.push({ url, init });
      return response(200, { user: { email: "u@x" } }, [`${ACCESS_COOKIE}=A1; HttpOnly`, `${REFRESH_COOKIE}=R1; HttpOnly`]);
    });
    const out = await client.refresh("R0");
    expect(out).toEqual({ ok: true, tokens: { accessToken: "A1", refreshToken: "R1", userEmail: "u@x" } });
    expect(calls[0]!.url).toBe("http://gw/portal/auth/refresh");
    expect(JSON.parse(String(calls[0]!.init?.body))).toEqual({ refresh_token: "R0" });
  });

  it("refresh: 401 refresh_reuse → rejected with the code", async () => {
    const client = new PortalAuthClient("http://gw", async () => response(401, { detail: { code: "refresh_reuse" } }));
    expect(await client.refresh("R0")).toEqual({ ok: false, kind: "rejected", status: 401, code: "refresh_reuse" });
  });

  it("refresh: 200 without cookies is rejected, not a session", async () => {
    const client = new PortalAuthClient("http://gw", async () => response(200, { error: "envelope" }));
    expect(await client.refresh("R0")).toMatchObject({ ok: false, kind: "rejected", status: 200 });
  });

  it("refresh: thrown fetch → network", async () => {
    const client = new PortalAuthClient("http://gw", async () => {
      throw new Error("ECONNREFUSED");
    });
    expect(await client.refresh("R0")).toEqual({ ok: false, kind: "network", message: "ECONNREFUSED" });
  });

  it("exchangeTicket: tokens from the body, nonce sent", async () => {
    let sent: unknown;
    const client = new PortalAuthClient("http://gw", async (_url, init) => {
      sent = JSON.parse(String(init?.body));
      return response(200, { access_token: "A", refresh_token: "R", user: { email: "u@x" } });
    });
    expect(await client.exchangeTicket("t".repeat(32), "n".repeat(43))).toEqual({ ok: true, tokens: { accessToken: "A", refreshToken: "R", userEmail: "u@x" } });
    expect(sent).toEqual({ ticket: "t".repeat(32), nonce: "n".repeat(43) });
  });

  it("logout: sends only the refresh cookie", async () => {
    let seen: RequestInit | undefined;
    const client = new PortalAuthClient("http://gw", async (_url, init) => {
      seen = init;
      return response(200, { message: "ok" });
    });
    expect(await client.logout("R0")).toEqual({ ok: true, status: 200 });
    expect((seen?.headers as Record<string, string>)["Cookie"]).toBe(`${REFRESH_COOKIE}=R0`);
    expect(seen?.body).toBeUndefined();
  });
});
