// FIX31 Part 2 — the host-side connector OAuth flow, against a DISPOSABLE
// in-test authorization server (this file only; nothing here is a runnable
// default, and no real client id, secret or token exists anywhere in the repo).
//
// Pins:
//   • nothing is configured or enabled by default: no file → every status is
//     not-configured, connect answers oauth-not-configured; a registration
//     without "enabled": true answers oauth-disabled and lists no active id;
//   • the flow: system browser (injected), PKCE S256 (the server verifies the
//     verifier against the challenge), loopback redirect, state checked;
//   • the token lands in the encrypted store (injected crypto) and is never in
//     the log or the status the page sees;
//   • the user denies at the provider → oauth-denied; closes the tab / takes
//     too long → oauth-timeout (the loopback listener is closed either way);
//     a callback with a foreign state is ignored, not accepted;
//   • a second Connect supersedes the first (oauth-cancelled);
//   • the access token refreshes when close to expiry, via the refresh grant;
//   • disconnect revokes at the provider (RFC 7009 form post) and deletes
//     locally — and deletes locally even when the provider's revoke fails;
//   • the registration file is validated strictly (https only; bad entries dropped).
import { createHash } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConnectorOAuth, ConnectorTokenStore, RegistrationFile, parseRegistrations, type OAuthRegistration, type TokenCrypto } from "../src/main/connector-oauth";
import type { Logger } from "../src/main/logger";

const b64url = (b: Buffer) => b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/** A throwaway "provider": /authorize redirects to the app's loopback with a
 *  code (or an error), /token checks PKCE + issues tokens, /revoke records. */
function authServer() {
  const state = { authorizeHits: 0, codes: new Map<string, { challenge: string; redirect: string }>(), tokens: 0, refreshes: 0, revoked: [] as string[], deny: false, revokeStatus: 200, expiresIn: 3600 as number | null, lastAuthorizeUrl: null as URL | null };
  const server = http.createServer(async (req, res) => {
    const u = new URL(req.url ?? "/", "http://x");
    if (u.pathname === "/authorize") {
      state.authorizeHits++;
      state.lastAuthorizeUrl = u;
      const redirect = new URL(u.searchParams.get("redirect_uri")!);
      redirect.searchParams.set("state", u.searchParams.get("state")!);
      if (state.deny) redirect.searchParams.set("error", "access_denied");
      else {
        const code = `code-${state.codes.size + 1}`;
        state.codes.set(code, { challenge: u.searchParams.get("code_challenge")!, redirect: redirect.origin + redirect.pathname });
        redirect.searchParams.set("code", code);
      }
      // "the browser follows the redirect": fetch the loopback ourselves
      const r = await fetch(redirect.toString());
      res.writeHead(200).end(`followed ${r.status}`);
      return;
    }
    let body = "";
    for await (const chunk of req) body += chunk;
    const form = new URLSearchParams(body);
    if (u.pathname === "/token") {
      if (form.get("grant_type") === "refresh_token") {
        state.refreshes++;
        if (form.get("refresh_token") !== "refresh-1") return void res.writeHead(400, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "invalid_grant" }));
        return void res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ access_token: "access-2", token_type: "Bearer", expires_in: 3600 }));
      }
      const c = state.codes.get(form.get("code") ?? "");
      const verifier = form.get("code_verifier") ?? "";
      const expected = c ? c.challenge : "";
      if (!c || b64url(createHash("sha256").update(verifier).digest()) !== expected || form.get("redirect_uri") !== c.redirect || form.get("client_id") !== "client-abc") {
        return void res.writeHead(400, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "invalid_grant" }));
      }
      state.tokens++;
      const payload: Record<string, unknown> = { access_token: "access-1", refresh_token: "refresh-1", token_type: "Bearer", scope: "chat:write" };
      if (state.expiresIn !== null) payload["expires_in"] = state.expiresIn;
      return void res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(payload));
    }
    if (u.pathname === "/revoke") {
      state.revoked.push(`${form.get("token_type_hint")}:${form.get("token")}`);
      return void res.writeHead(state.revokeStatus).end();
    }
    res.writeHead(404).end();
  });
  return { state, server, start: () => new Promise<number>((r) => server.listen(0, "127.0.0.1", () => r((server.address() as { port: number }).port))), stop: () => new Promise<void>((r) => server.close(() => r())) };
}

const xorCrypto: TokenCrypto = {
  available: () => true,
  encrypt: (s) => Buffer.from(Buffer.from(s, "utf8").map((b) => b ^ 0x5a)),
  decrypt: (b) => Buffer.from(b.map((x) => x ^ 0x5a)).toString("utf8"),
};

function reg(port: number, over: Partial<OAuthRegistration> = {}): OAuthRegistration {
  return {
    connectorId: "slack",
    clientId: "client-abc",
    clientSecret: null,
    authorizationEndpoint: `https://provider.test/authorize`,
    tokenEndpoint: `https://provider.test/token`,
    revocationEndpoint: `https://provider.test/revoke`,
    serverUrl: "https://mcp.provider.test/slack",
    scopes: ["chat:write"],
    extraAuthParams: {},
    enabled: true,
    ...over,
  };
}

/** Routes the registration's https URLs to the in-test server (the flow only
 *  ever sees https endpoints, as it would in the app). */
function fetchVia(port: number) {
  return (input: string, init?: RequestInit) => fetch(input.replace("https://provider.test", `http://127.0.0.1:${port}`), init);
}
/** "The system browser": opens the authorize URL by fetching it through the
 *  in-test server, which redirects to the loopback. */
function browserVia(port: number, log: string[]) {
  return async (url: string) => {
    log.push(url);
    void fetch(url.replace("https://provider.test", `http://127.0.0.1:${port}`)).catch(() => {});
  };
}

const lines: string[] = [];
const log: Logger = { info: (m) => lines.push(m), warn: (m) => lines.push(m), error: (m) => lines.push(m), filePath: null, attachFile: () => {}, lines: [] };

let tmp: string;
let srv: ReturnType<typeof authServer>;
let port: number;
beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-oauth-"));
  srv = authServer();
  port = await srv.start();
  lines.length = 0;
});
afterEach(async () => {
  await srv.stop();
  fs.rmSync(tmp, { recursive: true, force: true });
});

function flow(regs: Map<string, OAuthRegistration>, opened: string[] = [], timeoutMs = 10_000) {
  const tokens = new ConnectorTokenStore(path.join(tmp, "tokens"), xorCrypto);
  const oauth = new ConnectorOAuth({ registrations: () => regs, tokens, openExternal: browserVia(port, opened), log, fetchImpl: fetchVia(port), flowTimeoutMs: timeoutMs });
  return { oauth, tokens };
}

describe("FIX31 Part 2 — nothing is enabled unless the owner says so", () => {
  it("no registration file → nothing configured, connect refuses, no active connector", async () => {
    const file = new RegistrationFile(path.join(tmp, "connector-oauth.json"), log);
    const { oauth } = flow(file.current());
    expect(oauth.status("slack")).toMatchObject({ configured: false, enabled: false, connected: false });
    expect(oauth.activeConnectorIds()).toEqual([]);
    expect(await oauth.connect("slack")).toEqual({ ok: false, error: "oauth-not-configured" });
    expect(oauth.statuses(["slack", "gmail"]).map((s) => s.configured)).toEqual([false, false]);
  });

  it("a registration without enabled:true is configured but OFF — connect refuses, nothing is active", async () => {
    const regs = new Map([["slack", reg(port, { enabled: false })]]);
    const { oauth } = flow(regs);
    expect(oauth.status("slack")).toMatchObject({ configured: true, enabled: false, connected: false });
    expect(await oauth.connect("slack")).toEqual({ ok: false, error: "oauth-disabled" });
    expect(oauth.activeConnectorIds()).toEqual([]);
    expect(srv.state.authorizeHits).toBe(0);
  });

  it("the file is validated strictly: enabled defaults to false, http endpoints and bad entries are dropped", () => {
    const parsed = parseRegistrations(JSON.stringify({
      connectors: {
        slack: { clientId: "c", authorizationEndpoint: "https://a/x", tokenEndpoint: "https://a/t", serverUrl: "https://m/s" },
        bad1: { clientId: "c", authorizationEndpoint: "http://a/x", tokenEndpoint: "https://a/t", serverUrl: "https://m/s" },
        bad2: { clientId: "", authorizationEndpoint: "https://a/x", tokenEndpoint: "https://a/t", serverUrl: "https://m/s" },
        bad3: { clientId: "c", authorizationEndpoint: "https://a/x", tokenEndpoint: "https://a/t" },
        "Bad Id": { clientId: "c", authorizationEndpoint: "https://a/x", tokenEndpoint: "https://a/t", serverUrl: "https://m/s" },
      },
    }), log);
    expect([...parsed.keys()]).toEqual(["slack"]);
    expect(parsed.get("slack")!.enabled).toBe(false);
    expect(parseRegistrations("not json", log).size).toBe(0);
    expect(parseRegistrations("[]", log).size).toBe(0);
  });
});

describe("FIX31 Part 2 — the flow", () => {
  it("system browser + PKCE + loopback + state → token in the encrypted store; the page sees only 'connected'", async () => {
    const opened: string[] = [];
    const { oauth, tokens } = flow(new Map([["slack", reg(port)]]), opened);
    const r = await oauth.connect("slack");
    expect(r).toMatchObject({ ok: true, connected: true });
    // The browser got the authorize URL with PKCE S256, state, scope and the loopback redirect.
    expect(opened).toHaveLength(1);
    const u = new URL(opened[0]);
    expect(u.searchParams.get("code_challenge_method")).toBe("S256");
    expect(u.searchParams.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(u.searchParams.get("state")).toMatch(/^[A-Za-z0-9_-]{32}$/);
    expect(u.searchParams.get("redirect_uri")).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/callback$/);
    expect(u.searchParams.get("scope")).toBe("chat:write");
    expect(srv.state.tokens).toBe(1);
    // Stored encrypted; readable only through the store.
    const stored = tokens.get("slack")!;
    expect(stored.accessToken).toBe("access-1");
    expect(stored.refreshToken).toBe("refresh-1");
    const onDisk = fs.readFileSync(path.join(tmp, "tokens", "slack.token"));
    expect(onDisk.toString("utf8")).not.toContain("access-1");
    // Status: THAT it is connected — no token field at all.
    const st = oauth.status("slack");
    expect(st).toMatchObject({ configured: true, enabled: true, connected: true });
    expect(JSON.stringify(st)).not.toContain("access-1");
    expect(oauth.activeConnectorIds()).toEqual(["slack"]);
    // The log never carries a token either.
    expect(lines.join("\n")).not.toContain("access-1");
    expect(lines.join("\n")).not.toContain("refresh-1");
    expect(await oauth.accessToken("slack")).toBe("access-1");
  });

  it("the user denies at the provider → oauth-denied, nothing stored, listener closed", async () => {
    srv.state.deny = true;
    const { oauth, tokens } = flow(new Map([["slack", reg(port)]]));
    expect(await oauth.connect("slack")).toMatchObject({ ok: false, error: "oauth-denied" });
    expect(tokens.get("slack")).toBeNull();
    expect(oauth.status("slack").inFlight).toBe(false);
  });

  it("the tab is closed / the user walks away → oauth-timeout; a later stray callback finds no listener", async () => {
    const opened: string[] = [];
    // A browser that never completes the sign-in.
    const tokens = new ConnectorTokenStore(path.join(tmp, "tokens"), xorCrypto);
    const oauth = new ConnectorOAuth({ registrations: () => new Map([["slack", reg(port)]]), tokens, openExternal: async (u) => void opened.push(u), log, fetchImpl: fetchVia(port), flowTimeoutMs: 150 });
    const t0 = Date.now();
    expect(await oauth.connect("slack")).toEqual({ ok: false, error: "oauth-timeout" });
    expect(Date.now() - t0).toBeGreaterThanOrEqual(140);
    expect(tokens.get("slack")).toBeNull();
    const redirect = new URL(new URL(opened[0]).searchParams.get("redirect_uri")!);
    await expect(fetch(`${redirect.origin}/callback?code=x&state=y`)).rejects.toThrow(); // port closed
  });

  it("a callback with a foreign state is ignored, not accepted", async () => {
    const opened: string[] = [];
    const tokens = new ConnectorTokenStore(path.join(tmp, "tokens"), xorCrypto);
    const oauth = new ConnectorOAuth({ registrations: () => new Map([["slack", reg(port)]]), tokens, openExternal: async (u) => void opened.push(u), log, fetchImpl: fetchVia(port), flowTimeoutMs: 400 });
    const p = oauth.connect("slack");
    await new Promise((r) => setTimeout(r, 30));
    const redirect = new URL(new URL(opened[0]).searchParams.get("redirect_uri")!);
    const res = await fetch(`${redirect.origin}/callback?code=forged&state=not-ours`);
    expect(res.status).toBe(400);
    expect(await p).toEqual({ ok: false, error: "oauth-timeout" }); // still waiting for OURS, then times out
    expect(srv.state.tokens).toBe(0);
    expect(tokens.get("slack")).toBeNull();
  });

  it("a second Connect supersedes a hanging first one", async () => {
    const opened: string[] = [];
    const tokens = new ConnectorTokenStore(path.join(tmp, "tokens"), xorCrypto);
    let hang = true;
    const oauth = new ConnectorOAuth({ registrations: () => new Map([["slack", reg(port)]]), tokens, openExternal: async (u) => { opened.push(u); if (!hang) void fetch(u.replace("https://provider.test", `http://127.0.0.1:${port}`)).catch(() => {}); }, log, fetchImpl: fetchVia(port), flowTimeoutMs: 5000 });
    const first = oauth.connect("slack");
    await new Promise((r) => setTimeout(r, 20));
    hang = false;
    const second = await oauth.connect("slack");
    expect(await first).toEqual({ ok: false, error: "oauth-cancelled" });
    expect(second).toMatchObject({ ok: true, connected: true });
  });

  it("refreshes a token close to expiry through the refresh grant; a dead refresh token means 'reconnect'", async () => {
    srv.state.expiresIn = 30; // expires within the refresh skew
    const { oauth, tokens } = flow(new Map([["slack", reg(port)]]));
    expect(await oauth.connect("slack")).toMatchObject({ ok: true });
    expect(await oauth.accessToken("slack")).toBe("access-2");
    expect(srv.state.refreshes).toBe(1);
    expect(tokens.get("slack")!.refreshToken).toBe("refresh-1"); // kept when the refresh reply has none
    tokens.set("slack", { ...tokens.get("slack")!, accessToken: "stale", refreshToken: "refresh-dead", expiresAt: Date.now() - 1 });
    expect(await oauth.accessToken("slack")).toBeNull();
    expect(oauth.status("slack").connected).toBe(true); // still "connected" on the page; a call will say reconnect
  });

  it("disconnect revokes both tokens at the provider and deletes locally; deletes locally even if revoke fails", async () => {
    const { oauth, tokens } = flow(new Map([["slack", reg(port)]]));
    await oauth.connect("slack");
    expect(await oauth.disconnect("slack")).toEqual({ ok: true, revoked: true, hadToken: true });
    expect(srv.state.revoked.sort()).toEqual(["access_token:access-1", "refresh_token:refresh-1"]);
    expect(tokens.get("slack")).toBeNull();
    expect(oauth.status("slack").connected).toBe(false);
    expect(oauth.activeConnectorIds()).toEqual([]);

    await oauth.connect("slack");
    srv.state.revokeStatus = 500;
    expect(await oauth.disconnect("slack")).toEqual({ ok: true, revoked: false, hadToken: true });
    expect(tokens.get("slack")).toBeNull();
    expect(await oauth.disconnect("gmail")).toEqual({ ok: false, error: "oauth-not-configured" });
  });

  it("encrypted storage unavailable → refuses to connect rather than store plain text", async () => {
    const tokens = new ConnectorTokenStore(path.join(tmp, "tokens"), { ...xorCrypto, available: () => false });
    const oauth = new ConnectorOAuth({ registrations: () => new Map([["slack", reg(port)]]), tokens, openExternal: async () => {}, log, fetchImpl: fetchVia(port) });
    expect(await oauth.connect("slack")).toEqual({ ok: false, error: "oauth-storage-unavailable" });
    expect(srv.state.authorizeHits).toBe(0);
  });

  it("the registration file is re-read when it changes", () => {
    const file = path.join(tmp, "connector-oauth.json");
    const rf = new RegistrationFile(file, log);
    expect(rf.current().size).toBe(0);
    fs.writeFileSync(file, JSON.stringify({ connectors: { slack: { clientId: "c", authorizationEndpoint: "https://a/x", tokenEndpoint: "https://a/t", serverUrl: "https://m/s", enabled: true } } }));
    expect(rf.current().get("slack")?.enabled).toBe(true);
    fs.rmSync(file);
    expect(rf.current().size).toBe(0);
  });
});
