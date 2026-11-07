// Portal auth client — the gateway's /portal/auth/* endpoints, spoken from
// the MAIN process with plain fetch (no cookie jar: tokens are read out of
// Set-Cookie headers exactly as SranDesktop.Infrastructure/Api/PortalAuthClient
// does, and refresh is body-based). No 'electron' import so the parsing is
// unit-testable.
//
// Endpoints (backend/app/api/routes/portal_auth.py):
//   POST /portal/auth/refresh                 body {refresh_token} → Set-Cookie ×2 + {user}
//   POST /portal/auth/oauth/desktop/exchange  body {ticket, nonce} → {access_token, refresh_token, user}
//   POST /portal/auth/logout                  Cookie: portal_refresh_token → revokes jti, 200
//
// Nothing here ever logs a token. Fingerprints are sha256/8 of the value —
// enough to tell two tokens apart in a log, useless for anything else.

import { createHash } from "node:crypto";

export const ACCESS_COOKIE = "portal_access_token";
export const REFRESH_COOKIE = "portal_refresh_token";

export interface SessionTokens {
  readonly accessToken: string;
  readonly refreshToken: string;
  readonly userEmail: string | null;
}

export type AuthOutcome =
  | { readonly ok: true; readonly tokens: SessionTokens }
  | { readonly ok: false; readonly kind: "rejected"; readonly status: number; readonly code: string }
  | { readonly ok: false; readonly kind: "network"; readonly message: string };

export function fingerprint(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex").slice(0, 8);
}

/** name → value for every Set-Cookie header on a response. */
export function parseSetCookies(setCookieHeaders: readonly string[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const header of setCookieHeaders) {
    const first = header.split(";", 1)[0] ?? "";
    const eq = first.indexOf("=");
    if (eq <= 0) continue;
    const name = first.slice(0, eq).trim();
    const value = first.slice(eq + 1).trim();
    if (name) out.set(name, value);
  }
  return out;
}

export function tokensFromSetCookies(setCookieHeaders: readonly string[], userEmail: string | null): SessionTokens | null {
  const cookies = parseSetCookies(setCookieHeaders);
  const access = cookies.get(ACCESS_COOKIE);
  const refresh = cookies.get(REFRESH_COOKIE);
  if (!access || !refresh) return null;
  return { accessToken: access, refreshToken: refresh, userEmail };
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export class PortalAuthClient {
  constructor(
    private readonly gatewayUrl: string,
    private readonly fetchImpl: FetchLike = (input, init) => fetch(input, init),
    private readonly timeoutMs = 15_000,
  ) {}

  /** Rotate: the stored refresh token → a fresh pair (Set-Cookie). */
  async refresh(refreshToken: string): Promise<AuthOutcome> {
    return this.call("/portal/auth/refresh", { refresh_token: refreshToken }, (res, body) =>
      tokensFromSetCookies(res.headers.getSetCookie(), emailOf(body)),
    );
  }

  /** Redeem the one-time deep-link ticket (tokens in the BODY, by design). */
  async exchangeTicket(ticket: string, nonce: string): Promise<AuthOutcome> {
    return this.call("/portal/auth/oauth/desktop/exchange", { ticket, nonce }, (_res, body) => {
      const access = body["access_token"];
      const refresh = body["refresh_token"];
      if (typeof access !== "string" || typeof refresh !== "string" || !access || !refresh) return null;
      return { accessToken: access, refreshToken: refresh, userEmail: emailOf(body) };
    });
  }

  /** Server-side revocation of the refresh token's jti. */
  async logout(refreshToken: string): Promise<{ ok: boolean; status: number }> {
    try {
      const res = await this.fetchImpl(`${this.gatewayUrl}/portal/auth/logout`, {
        method: "POST",
        headers: { Cookie: `${REFRESH_COOKIE}=${refreshToken}` },
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      return { ok: res.ok, status: res.status };
    } catch {
      return { ok: false, status: 0 };
    }
  }

  private async call(
    path: string,
    body: Record<string, string>,
    extract: (res: Response, body: Record<string, unknown>) => SessionTokens | null,
  ): Promise<AuthOutcome> {
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.gatewayUrl}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8", Accept: "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (err) {
      return { ok: false, kind: "network", message: (err as Error).message };
    }
    let json: Record<string, unknown> = {};
    try {
      json = (await res.json()) as Record<string, unknown>;
    } catch {
      /* non-JSON body: fine for a failure, handled below */
    }
    if (!res.ok) {
      return { ok: false, kind: "rejected", status: res.status, code: errorCodeOf(json) };
    }
    const tokens = extract(res, json);
    if (tokens === null) {
      // 200 without tokens is the "200-with-error-envelope" trap: treat as rejected.
      return { ok: false, kind: "rejected", status: res.status, code: errorCodeOf(json) || "no_tokens_in_response" };
    }
    return { ok: true, tokens };
  }
}

function emailOf(body: Record<string, unknown>): string | null {
  const user = body["user"];
  if (user && typeof user === "object" && typeof (user as Record<string, unknown>)["email"] === "string") {
    return (user as Record<string, string>)["email"] ?? null;
  }
  return null;
}

function errorCodeOf(body: Record<string, unknown>): string {
  for (const key of ["code", "error", "detail"]) {
    const v = body[key];
    if (typeof v === "string") return v;
    if (v && typeof v === "object" && typeof (v as Record<string, unknown>)["code"] === "string") {
      return (v as Record<string, string>)["code"] ?? "";
    }
  }
  return "";
}
