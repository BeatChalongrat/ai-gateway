// FIX31 Part 2 — connector OAuth, HOST-side. Built, tested, switched off.
//
// The token for a third-party connector (Slack, Gmail, Drive…) belongs to the
// process that owns privileged capability — this host — never to the sidecar
// that runs tools and never to the page. This module is the RFC 8252 native-app
// flow we already use for login, applied per connector:
//
//   • the SYSTEM browser (shell.openExternal, injected) — never an in-app
//     window: an embedded browser for a third-party login is a
//     credential-harvesting pattern and we do not ship one;
//   • PKCE S256 + a random `state`, checked on the way back;
//   • a loopback redirect on 127.0.0.1:<ephemeral>, one callback, then closed;
//   • tokens in the host's encrypted store (safeStorage, injected), scoped per
//     connector; `status()` tells the page THAT a connector is connected and
//     never the token; `accessToken()` is for the host's own MCP client only;
//   • disconnect revokes at the provider (RFC 7009 when an endpoint is
//     configured) and deletes locally regardless.
//
// What is NOT here, by the brief: no registration, no client id or secret, no
// connector enabled. Registrations arrive from the owner through ONE file —
// <userData>/connector-oauth.json — and a connector takes part only when its
// entry says `"enabled": true`. With no file, every status is "not configured"
// and every connect answers oauth-not-configured. There is no env var, no
// default, no catalogue row that can turn this on. Nothing here is logged
// beyond connector ids, endpoints' hosts and outcomes.
//
// No 'electron' import: unit-tested in plain Node against a disposable
// in-test authorization server (which is test code, not a runnable default).

import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

import type { Logger } from "./logger";
import { oauthCallbackPage } from "./native-dialogs";

export const CONNECTOR_OAUTH_FILENAME = "connector-oauth.json";
const DEFAULT_FLOW_TIMEOUT_MS = 10 * 60_000;
/** Refresh when this close to expiry (or already past it). */
const REFRESH_SKEW_MS = 60_000;

export interface OAuthRegistration {
  readonly connectorId: string;
  readonly clientId: string;
  /** Optional: PKCE public clients have none. Some providers (Slack, Google
   *  "installed app") still issue one; it is read from the file and never
   *  logged, echoed to the page, or written anywhere else. */
  readonly clientSecret: string | null;
  readonly authorizationEndpoint: string;
  readonly tokenEndpoint: string;
  readonly revocationEndpoint: string | null;
  /** The connector's MCP server (https). The embedded catalogue's server_url
   *  values are placeholders; the owner names the real one here. */
  readonly serverUrl: string;
  readonly scopes: readonly string[];
  /** Provider-specific extra query parameters on the authorize URL
   *  (e.g. Google's access_type=offline, Slack's user_scope). */
  readonly extraAuthParams: Readonly<Record<string, string>>;
  /** The switch. Default false; only the owner's file can set it. */
  readonly enabled: boolean;
}

export interface TokenRecord {
  accessToken: string;
  refreshToken: string | null;
  tokenType: string;
  /** epoch ms, or null when the provider gave no expires_in */
  expiresAt: number | null;
  scope: string | null;
  obtainedAt: number;
}

export interface ConnectorStatus {
  connectorId: string;
  configured: boolean;
  enabled: boolean;
  connected: boolean;
  expiresAt: number | null;
  scopes: readonly string[];
  inFlight: boolean;
}

export type ConnectError =
  | "oauth-not-configured"
  | "oauth-disabled"
  | "oauth-browser-failed"
  | "oauth-denied"
  | "oauth-timeout"
  | "oauth-cancelled"
  | "oauth-state-mismatch"
  | "oauth-exchange-failed"
  | "oauth-storage-unavailable";

export type ConnectResult = { ok: true; connected: true; expiresAt: number | null } | { ok: false; error: ConnectError; detail?: string };

// ── registrations (the owner's file) ────────────────────────────────────────

function isHttpsUrl(v: unknown): v is string {
  if (typeof v !== "string") return false;
  try {
    const u = new URL(v);
    return u.protocol === "https:";
  } catch {
    return false;
  }
}

/** Strict: a malformed entry is dropped (and named in the log), never
 *  half-loaded. A missing file is the normal, empty state. */
export function parseRegistrations(raw: string, log?: Pick<Logger, "warn">): Map<string, OAuthRegistration> {
  const out = new Map<string, OAuthRegistration>();
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch (err) {
    log?.warn(`connector-oauth: file is not JSON (${(err as Error).message}); no connector configured`);
    return out;
  }
  const entries = (json as { connectors?: unknown })?.connectors;
  if (!entries || typeof entries !== "object" || Array.isArray(entries)) {
    log?.warn("connector-oauth: expected { \"connectors\": { <id>: {...} } }; no connector configured");
    return out;
  }
  for (const [id, v] of Object.entries(entries as Record<string, unknown>)) {
    const e = (v ?? {}) as Record<string, unknown>;
    const problems: string[] = [];
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(id)) problems.push("id");
    if (typeof e["clientId"] !== "string" || !(e["clientId"] as string).trim()) problems.push("clientId");
    if (!isHttpsUrl(e["authorizationEndpoint"])) problems.push("authorizationEndpoint (https)");
    if (!isHttpsUrl(e["tokenEndpoint"])) problems.push("tokenEndpoint (https)");
    if (!isHttpsUrl(e["serverUrl"])) problems.push("serverUrl (https)");
    if (e["revocationEndpoint"] !== undefined && e["revocationEndpoint"] !== null && !isHttpsUrl(e["revocationEndpoint"])) problems.push("revocationEndpoint (https)");
    if (e["scopes"] !== undefined && (!Array.isArray(e["scopes"]) || !(e["scopes"] as unknown[]).every((s) => typeof s === "string"))) problems.push("scopes");
    if (e["clientSecret"] !== undefined && e["clientSecret"] !== null && typeof e["clientSecret"] !== "string") problems.push("clientSecret");
    if (e["enabled"] !== undefined && typeof e["enabled"] !== "boolean") problems.push("enabled");
    const extra = e["extraAuthParams"];
    if (extra !== undefined && (!extra || typeof extra !== "object" || Array.isArray(extra) || !Object.values(extra as object).every((x) => typeof x === "string"))) problems.push("extraAuthParams");
    if (problems.length) {
      log?.warn(`connector-oauth: entry '${id}' ignored — invalid ${problems.join(", ")}`);
      continue;
    }
    out.set(id, {
      connectorId: id,
      clientId: (e["clientId"] as string).trim(),
      clientSecret: typeof e["clientSecret"] === "string" && e["clientSecret"] ? e["clientSecret"] : null,
      authorizationEndpoint: e["authorizationEndpoint"] as string,
      tokenEndpoint: e["tokenEndpoint"] as string,
      revocationEndpoint: typeof e["revocationEndpoint"] === "string" ? e["revocationEndpoint"] : null,
      serverUrl: e["serverUrl"] as string,
      scopes: Array.isArray(e["scopes"]) ? [...(e["scopes"] as string[])] : [],
      extraAuthParams: extra && typeof extra === "object" ? { ...(extra as Record<string, string>) } : {},
      enabled: e["enabled"] === true,
    });
  }
  return out;
}

export function loadRegistrations(filePath: string, log?: Pick<Logger, "warn" | "info">): Map<string, OAuthRegistration> {
  if (!fs.existsSync(filePath)) return new Map();
  let raw: string;
  try {
    raw = fs.readFileSync(filePath, "utf8");
  } catch (err) {
    log?.warn(`connector-oauth: cannot read ${filePath}: ${(err as Error).message}`);
    return new Map();
  }
  const regs = parseRegistrations(raw, log);
  const enabled = [...regs.values()].filter((r) => r.enabled).map((r) => r.connectorId);
  log?.info(`connector-oauth: ${regs.size} registration(s) in ${filePath}; enabled: ${enabled.length ? enabled.join(", ") : "none"}`);
  return regs;
}

/** The owner's file, re-read when it changes (mtime), logged once per change.
 *  Missing file = no registrations = nothing configured. */
export class RegistrationFile {
  private cache: Map<string, OAuthRegistration> = new Map();
  private stamp: string | null = null;
  constructor(
    readonly filePath: string,
    private readonly log: Pick<Logger, "warn" | "info">,
  ) {}
  current(): Map<string, OAuthRegistration> {
    let stamp: string;
    try {
      const st = fs.statSync(this.filePath);
      stamp = `${st.mtimeMs}:${st.size}`;
    } catch {
      stamp = "absent";
    }
    if (stamp !== this.stamp) {
      this.stamp = stamp;
      this.cache = stamp === "absent" ? new Map() : loadRegistrations(this.filePath, this.log);
      if (stamp === "absent") this.log.info(`connector-oauth: no ${path.basename(this.filePath)} — no connector configured`);
    }
    return this.cache;
  }
}

// ── the encrypted token store ───────────────────────────────────────────────

export interface TokenCrypto {
  available(): boolean;
  encrypt(plain: string): Buffer;
  decrypt(blob: Buffer): string;
}

/** One file per connector under <dir>, encrypted with the injected crypto
 *  (Electron safeStorage in the app). Nothing is kept in plain text. */
export class ConnectorTokenStore {
  constructor(
    private readonly dir: string,
    private readonly crypto: TokenCrypto,
  ) {}

  private file(id: string): string {
    return path.join(this.dir, `${id}.token`);
  }

  available(): boolean {
    return this.crypto.available();
  }

  get(id: string): TokenRecord | null {
    try {
      const f = this.file(id);
      if (!fs.existsSync(f) || !this.crypto.available()) return null;
      const rec = JSON.parse(this.crypto.decrypt(fs.readFileSync(f))) as TokenRecord;
      return typeof rec?.accessToken === "string" && rec.accessToken ? rec : null;
    } catch {
      return null;
    }
  }

  set(id: string, rec: TokenRecord): void {
    fs.mkdirSync(this.dir, { recursive: true });
    fs.writeFileSync(this.file(id), this.crypto.encrypt(JSON.stringify(rec)));
  }

  delete(id: string): boolean {
    const f = this.file(id);
    const existed = fs.existsSync(f);
    fs.rmSync(f, { force: true });
    return existed;
  }

  list(): string[] {
    try {
      return fs.readdirSync(this.dir).filter((n) => n.endsWith(".token")).map((n) => n.slice(0, -".token".length));
    } catch {
      return [];
    }
  }
}

// ── the flow ────────────────────────────────────────────────────────────────

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ConnectorOAuthDeps {
  registrations: () => Map<string, OAuthRegistration>;
  tokens: ConnectorTokenStore;
  /** The SYSTEM browser. Electron's shell.openExternal in the app. */
  openExternal: (url: string) => Promise<void>;
  log: Logger;
  fetchImpl?: FetchLike;
  flowTimeoutMs?: number;
  /** Tests bind a fixed host; the app binds 127.0.0.1. */
  loopbackHost?: string;
}

interface InFlight {
  connectorId: string;
  server: http.Server;
  cancel: (reason: ConnectError) => void;
}

const b64url = (b: Buffer) => b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

// AM10 F — the page the loopback listener answers with is built by
// native-dialogs.ts (oauthCallbackPage) in the current language.

export class ConnectorOAuth {
  private inFlight: InFlight | null = null;
  private readonly fetchImpl: FetchLike;
  private readonly flowTimeoutMs: number;
  private readonly loopbackHost: string;

  constructor(private readonly deps: ConnectorOAuthDeps) {
    this.fetchImpl = deps.fetchImpl ?? ((input, init) => fetch(input, init));
    this.flowTimeoutMs = deps.flowTimeoutMs ?? DEFAULT_FLOW_TIMEOUT_MS;
    this.loopbackHost = deps.loopbackHost ?? "127.0.0.1";
  }

  /** What the page may know: configured / enabled / connected — never a token. */
  status(connectorId: string): ConnectorStatus {
    const reg = this.deps.registrations().get(connectorId) ?? null;
    const tok = reg ? this.deps.tokens.get(connectorId) : null;
    return {
      connectorId,
      configured: reg !== null,
      enabled: reg?.enabled === true,
      connected: tok !== null,
      expiresAt: tok?.expiresAt ?? null,
      scopes: reg?.scopes ?? [],
      inFlight: this.inFlight?.connectorId === connectorId,
    };
  }

  statuses(ids: Iterable<string>): ConnectorStatus[] {
    const set = new Set<string>(ids);
    for (const id of this.deps.registrations().keys()) set.add(id);
    return [...set].sort().map((id) => this.status(id));
  }

  /** The ids that can take part in a turn: enabled AND connected. */
  activeConnectorIds(): string[] {
    return [...this.deps.registrations().values()].filter((r) => r.enabled && this.deps.tokens.get(r.connectorId) !== null).map((r) => r.connectorId);
  }

  registration(connectorId: string): OAuthRegistration | null {
    return this.deps.registrations().get(connectorId) ?? null;
  }

  cancelAll(reason: ConnectError = "oauth-cancelled"): void {
    this.inFlight?.cancel(reason);
  }

  async connect(connectorId: string): Promise<ConnectResult> {
    const reg = this.deps.registrations().get(connectorId);
    if (!reg) return { ok: false, error: "oauth-not-configured" };
    if (!reg.enabled) return { ok: false, error: "oauth-disabled" };
    if (!this.deps.tokens.available()) return { ok: false, error: "oauth-storage-unavailable" };
    // A second Connect while one is open (the user closed the tab and clicked
    // again) supersedes the first — its callback would fail the state check
    // anyway, and one loopback listener at a time is enough.
    this.inFlight?.cancel("oauth-cancelled");

    const verifier = b64url(randomBytes(32));
    const challenge = b64url(createHash("sha256").update(verifier).digest());
    const state = b64url(randomBytes(24));

    let server: http.Server;
    let port: number;
    try {
      ({ server, port } = await this.listen());
    } catch (err) {
      return { ok: false, error: "oauth-browser-failed", detail: `loopback listen failed: ${(err as Error).message}` };
    }
    const redirectUri = `http://${this.loopbackHost}:${port}/callback`;
    const url = new URL(reg.authorizationEndpoint);
    url.searchParams.set("response_type", "code");
    url.searchParams.set("client_id", reg.clientId);
    url.searchParams.set("redirect_uri", redirectUri);
    url.searchParams.set("code_challenge", challenge);
    url.searchParams.set("code_challenge_method", "S256");
    url.searchParams.set("state", state);
    if (reg.scopes.length) url.searchParams.set("scope", reg.scopes.join(" "));
    for (const [k, v] of Object.entries(reg.extraAuthParams)) url.searchParams.set(k, v);

    const callback = new Promise<{ ok: true; code: string } | { ok: false; error: ConnectError; detail?: string }>((resolve) => {
      let settled = false;
      const finish = (r: { ok: true; code: string } | { ok: false; error: ConnectError; detail?: string }) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        server.close();
        if (this.inFlight?.server === server) this.inFlight = null;
        resolve(r);
      };
      const timer = setTimeout(() => finish({ ok: false, error: "oauth-timeout" }), this.flowTimeoutMs);
      this.inFlight = { connectorId, server, cancel: (reason) => finish({ ok: false, error: reason }) };
      server.on("request", (req, res) => {
        const u = new URL(req.url ?? "/", `http://${this.loopbackHost}`);
        if (req.method !== "GET" || u.pathname !== "/callback") {
          res.writeHead(404, { "Content-Type": "text/plain" }).end("not found");
          return;
        }
        const gotState = u.searchParams.get("state");
        if (gotState !== state) {
          // Not ours (a stale tab, or someone poking the port). Say so, keep waiting.
          res.writeHead(400, { "Content-Type": "text/html; charset=utf-8" }).end(oauthCallbackPage("mismatch"));
          this.deps.log.warn(`connector-oauth ${connectorId}: callback with a foreign state — ignored`);
          return;
        }
        const err = u.searchParams.get("error");
        if (err) {
          res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }).end(oauthCallbackPage("notConnected"));
          finish({ ok: false, error: "oauth-denied", detail: err });
          return;
        }
        const code = u.searchParams.get("code");
        if (!code) {
          res.writeHead(400, { "Content-Type": "text/html; charset=utf-8" }).end(oauthCallbackPage("incomplete"));
          finish({ ok: false, error: "oauth-exchange-failed", detail: "no code in callback" });
          return;
        }
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }).end(oauthCallbackPage("connected"));
        finish({ ok: true, code });
      });
    });

    this.deps.log.info(`connector-oauth ${connectorId}: opening the system browser at ${url.host} (loopback :${port})`);
    try {
      await this.deps.openExternal(url.toString());
    } catch (err) {
      this.inFlight?.cancel("oauth-browser-failed");
      return { ok: false, error: "oauth-browser-failed", detail: (err as Error).message };
    }

    const cb = await callback;
    if (!cb.ok) {
      this.deps.log.info(`connector-oauth ${connectorId}: ${cb.error}${cb.detail ? ` (${cb.detail})` : ""}`);
      return cb;
    }
    const tok = await this.exchange(reg, { grant_type: "authorization_code", code: cb.code, redirect_uri: redirectUri, code_verifier: verifier });
    if (!tok.ok) {
      this.deps.log.warn(`connector-oauth ${connectorId}: token exchange failed (${tok.detail})`);
      return { ok: false, error: "oauth-exchange-failed", detail: tok.detail };
    }
    this.deps.tokens.set(connectorId, tok.record);
    this.deps.log.info(`connector-oauth ${connectorId}: connected${tok.record.expiresAt ? `, expires ${new Date(tok.record.expiresAt).toISOString()}` : ""}${tok.record.refreshToken ? ", refreshable" : ""}`);
    return { ok: true, connected: true, expiresAt: tok.record.expiresAt };
  }

  /** Revoke at the provider when it has an endpoint, delete locally always. */
  async disconnect(connectorId: string): Promise<{ ok: true; revoked: boolean; hadToken: boolean } | { ok: false; error: ConnectError }> {
    const reg = this.deps.registrations().get(connectorId);
    if (!reg) return { ok: false, error: "oauth-not-configured" };
    if (this.inFlight?.connectorId === connectorId) this.inFlight.cancel("oauth-cancelled");
    const tok = this.deps.tokens.get(connectorId);
    let revoked = false;
    if (tok && reg.revocationEndpoint) {
      for (const [token, hint] of [[tok.refreshToken, "refresh_token"], [tok.accessToken, "access_token"]] as const) {
        if (!token) continue;
        try {
          const body = new URLSearchParams({ token, token_type_hint: hint, client_id: reg.clientId });
          if (reg.clientSecret) body.set("client_secret", reg.clientSecret);
          const res = await this.fetchImpl(reg.revocationEndpoint, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded", Authorization: `Bearer ${tok.accessToken}` },
            body: body.toString(),
            signal: AbortSignal.timeout(15_000),
          });
          if (res.ok) revoked = true;
          else this.deps.log.warn(`connector-oauth ${connectorId}: revoke (${hint}) answered ${res.status}`);
        } catch (err) {
          this.deps.log.warn(`connector-oauth ${connectorId}: revoke (${hint}) failed: ${(err as Error).message}`);
        }
      }
    }
    const hadToken = this.deps.tokens.delete(connectorId);
    this.deps.log.info(`connector-oauth ${connectorId}: disconnected (revoked=${revoked}, local token ${hadToken ? "deleted" : "absent"})`);
    return { ok: true, revoked, hadToken };
  }

  /** For the host's own MCP client. Refreshes when close to expiry; null when
   *  not connected or the refresh fails (the caller reports "not connected"). */
  async accessToken(connectorId: string): Promise<string | null> {
    const reg = this.deps.registrations().get(connectorId);
    const tok = this.deps.tokens.get(connectorId);
    if (!reg || !reg.enabled || !tok) return null;
    if (tok.expiresAt === null || tok.expiresAt - Date.now() > REFRESH_SKEW_MS) return tok.accessToken;
    if (!tok.refreshToken) {
      this.deps.log.warn(`connector-oauth ${connectorId}: token expired and no refresh token — reconnect needed`);
      return null;
    }
    const r = await this.exchange(reg, { grant_type: "refresh_token", refresh_token: tok.refreshToken });
    if (!r.ok) {
      this.deps.log.warn(`connector-oauth ${connectorId}: refresh failed (${r.detail}) — reconnect needed`);
      return null;
    }
    const merged: TokenRecord = { ...r.record, refreshToken: r.record.refreshToken ?? tok.refreshToken };
    this.deps.tokens.set(connectorId, merged);
    this.deps.log.info(`connector-oauth ${connectorId}: token refreshed`);
    return merged.accessToken;
  }

  private async listen(): Promise<{ server: http.Server; port: number }> {
    const server = http.createServer();
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, this.loopbackHost, () => resolve());
    });
    const addr = server.address();
    if (!addr || typeof addr === "string") throw new Error("no loopback address");
    return { server, port: addr.port };
  }

  private async exchange(reg: OAuthRegistration, params: Record<string, string>): Promise<{ ok: true; record: TokenRecord } | { ok: false; detail: string }> {
    const body = new URLSearchParams({ ...params, client_id: reg.clientId });
    if (reg.clientSecret) body.set("client_secret", reg.clientSecret);
    let res: Response;
    try {
      res = await this.fetchImpl(reg.tokenEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
        body: body.toString(),
        signal: AbortSignal.timeout(30_000),
      });
    } catch (err) {
      return { ok: false, detail: `network: ${(err as Error).message}` };
    }
    let json: Record<string, unknown> = {};
    try {
      json = (await res.json()) as Record<string, unknown>;
    } catch {
      /* handled below */
    }
    if (!res.ok || json["ok"] === false) {
      // Status + the provider's error CODE only — never the body (it may echo the request).
      return { ok: false, detail: `${res.status}${typeof json["error"] === "string" ? ` ${json["error"]}` : ""}` };
    }
    // Standard shape, plus Slack's OAuth v2 (a user token under authed_user).
    const authedUser = json["authed_user"] as Record<string, unknown> | undefined;
    const access = typeof json["access_token"] === "string" ? json["access_token"] : typeof authedUser?.["access_token"] === "string" ? (authedUser["access_token"] as string) : null;
    if (!access) return { ok: false, detail: "no access_token in the token response" };
    const expiresIn = typeof json["expires_in"] === "number" ? json["expires_in"] : typeof authedUser?.["expires_in"] === "number" ? (authedUser["expires_in"] as number) : null;
    const refresh = typeof json["refresh_token"] === "string" ? json["refresh_token"] : typeof authedUser?.["refresh_token"] === "string" ? (authedUser["refresh_token"] as string) : null;
    return {
      ok: true,
      record: {
        accessToken: access,
        refreshToken: refresh,
        tokenType: typeof json["token_type"] === "string" ? json["token_type"] : "Bearer",
        expiresAt: expiresIn !== null ? Date.now() + expiresIn * 1000 : null,
        scope: typeof json["scope"] === "string" ? json["scope"] : typeof authedUser?.["scope"] === "string" ? (authedUser["scope"] as string) : null,
        obtainedAt: Date.now(),
      },
    };
  }
}
