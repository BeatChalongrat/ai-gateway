// The host owns the session (E2). Ported from the WebView2 shell's
// ShellSessionController (docs/SHELL_PHASE2_REPORT.md) — same ladder, same
// guards — with Electron's primitives underneath:
//
//   safeStorage           instead of DPAPI            (session-store.ts)
//   session.cookies       instead of CoreWebView2CookieManager
//   shell.openExternal    instead of IBrowserLauncher
//
// The ladder:
//   boot     stored session? → refresh (server round-trip) → inject → page.
//            none/rejected  → system-browser sign-in (nonce-bound ticket).
//   timer    rotate every sessionRefreshMinutes (< access TTL); network
//            error → retry in 1 min; 401/403 → clear → signed-out view.
//   redirect the page bounced to the portal (refused by the navigation
//            guard) → single-flight refresh → re-inject → reload; refresh
//            rejected → interactive sign-in; 3 redirects/120 s → give up.
//   logout   host (tray) → revoke + clear + blank + signed-out view.
//            page (its own POST /portal/auth/logout observed, then the portal
//            bounce) → recognised as LOGOUT intent, not re-auth → same clear.
//
// The page is never told anything. Session state reaches it as cookies in
// the jar it already reads from — no bridge, no IPC (E2 §4.8).
// Nothing here logs a token; sha256/8 fingerprints only.

import { randomBytes } from "node:crypto";
import type { Cookies } from "electron";
import type { Logger } from "./logger";
import { ACCESS_COOKIE, PortalAuthClient, REFRESH_COOKIE, fingerprint, type SessionTokens } from "./portal-auth";
import { SessionStore } from "./session-store";
import { parseLoginTicket } from "./deep-link";

export type ViewState = "signed-out" | "signing-in";

/** What the controller needs from the window — supplied by index.ts. */
export interface SessionSurface {
  showView(state: ViewState, reason?: string): void;
  loadPage(): Promise<void>;
  reloadPage(): void;
  blankPage(): Promise<void>;
  openExternal(url: string): Promise<void>;
}

export interface SessionControllerOptions {
  gatewayUrl: string;
  oauthProvider: string;
  oauthClient: string;
  scheme: string;
  refreshMinutes: number;
  signInTimeoutSeconds: number;
  /** Access-cookie lifetime as the server mints it (config.py:131). */
  accessTtlMinutes?: number;
  refreshTtlDays?: number;
  /** DEV PROBE ONLY (index.ts gates it on !app.isPackaged): write each
   * sign-in nonce to this file so a test can mint a server-side ticket bound
   * to it — the Google step substituted, everything after it real. */
  nonceExportPath?: string;
}

const LOGOUT_INTENT_WINDOW_MS = 10_000;
const LOOP_GUARD_MAX = 3;
const LOOP_GUARD_WINDOW_MS = 120_000;
const NETWORK_RETRY_MS = 60_000;

export class SessionController {
  private tokens: SessionTokens | null = null;
  private rotationCount = 0;
  private rotationTimer: NodeJS.Timeout | null = null;
  private inFlight: Promise<boolean> | null = null;
  private pendingSignIn: { nonce: string; timer: NodeJS.Timeout } | null = null;
  private recentRedirects: number[] = [];
  private logoutIntentAt = 0;
  private stopped = false;

  constructor(
    private readonly opts: SessionControllerOptions,
    private readonly store: SessionStore,
    private readonly cookies: Cookies,
    private readonly auth: PortalAuthClient,
    private readonly surface: SessionSurface,
    private readonly log: Logger,
  ) {}

  get hasSession(): boolean {
    return this.tokens !== null;
  }
  get rotations(): number {
    return this.rotationCount;
  }
  get signInPending(): boolean {
    return this.pendingSignIn !== null;
  }
  /** For the server-revocation probe only: the host's own copy. Never logged. */
  currentRefreshToken(): string | null {
    return this.tokens?.refreshToken ?? null;
  }
  /** The portal access token, for the host's OWN calls to the gateway's
   * portal routes and to chat-edge (E3). Read per call — rotation replaces
   * it. Never logged, never sent to the page. */
  currentAccessToken(): string | null {
    return this.tokens?.accessToken ?? null;
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  /** Authenticate first, navigate second. Returns true when the page may load. */
  async ensureSessionAtBoot(): Promise<boolean> {
    let stored: SessionTokens | null = null;
    try {
      stored = this.store.load();
    } catch (err) {
      this.log.warn(`BOOT: stored session unreadable (${(err as Error).message}) — treating as absent`);
    }
    if (stored === null) {
      // safeStorage is the source of truth; whatever the jar holds is not a
      // session. (Session cookies do not persist, so this is belt-and-braces.)
      await this.clearJar();
      this.log.info("BOOT: no stored session — starting the system-browser sign-in");
      await this.startSignIn();
      return false;
    }
    this.log.info(`BOOT: stored session found (refresh sha256/8=${fingerprint(stored.refreshToken)}) — validating with a server round-trip`);
    this.tokens = stored;
    const ok = await this.rotate("boot");
    if (ok) return true;
    if (this.tokens === null) {
      // Rejected: the store was cleared by rotate(); sign in interactively.
      this.log.info("BOOT: stored session rejected by the server — starting the system-browser sign-in");
      await this.startSignIn();
    } else {
      // Network: keep the stored session, show the signed-out view; "Sign in"
      // retries the restore first (see signInFromView).
      this.surface.showView("signed-out", "The gateway could not be reached. Check the connection and try again.");
    }
    return false;
  }

  /** The signed-out view's Sign-in control (an intent URL, not IPC). */
  async signInFromView(): Promise<void> {
    if (this.tokens !== null) {
      // A stored session survived a network failure at boot — try it first.
      if (await this.rotate("retry")) {
        this.startRotation();
        await this.surface.loadPage();
        return;
      }
      if (this.tokens !== null) {
        this.surface.showView("signed-out", "Still cannot reach the gateway.");
        return;
      }
    }
    await this.startSignIn();
  }

  // ── interactive sign-in (RFC 8252-style, nonce-bound one-time ticket) ──────
  async startSignIn(): Promise<void> {
    this.cancelSignIn(false);
    const nonce = randomBytes(32).toString("base64url"); // 43 chars, [A-Za-z0-9_-]
    const url =
      `${this.opts.gatewayUrl}/portal/auth/oauth/start?provider=${encodeURIComponent(this.opts.oauthProvider)}` +
      `&client=${encodeURIComponent(this.opts.oauthClient)}&nonce=${nonce}`;
    const timer = setTimeout(() => {
      this.log.warn(`SIGN-IN TIMED OUT after ${this.opts.signInTimeoutSeconds}s — no ticket arrived; signed-out view`);
      this.pendingSignIn = null;
      this.surface.showView("signed-out", "Sign-in timed out. Try again.");
    }, this.opts.signInTimeoutSeconds * 1000);
    this.pendingSignIn = { nonce, timer };
    if (this.opts.nonceExportPath) {
      require("node:fs").writeFileSync(this.opts.nonceExportPath, nonce, { encoding: "utf8", mode: 0o600 });
      this.log.warn(`PROBE: sign-in nonce exported to ${this.opts.nonceExportPath} (dev only)`);
    }
    this.surface.showView("signing-in");
    this.log.info(`SIGN-IN STARTED: opening the system browser at ${this.opts.gatewayUrl}/portal/auth/oauth/start (provider=${this.opts.oauthProvider}, client=${this.opts.oauthClient}, nonce in memory only)`);
    try {
      await this.surface.openExternal(url);
    } catch (err) {
      this.log.error(`SIGN-IN: could not open the system browser: ${(err as Error).message}`);
      this.cancelSignIn(true);
    }
  }

  cancelSignIn(showView = true): void {
    if (this.pendingSignIn) {
      clearTimeout(this.pendingSignIn.timer);
      this.pendingSignIn = null;
      this.log.info("SIGN-IN CANCELLED (nonce discarded)");
    }
    if (showView) this.surface.showView("signed-out");
  }

  /** A deep link arrived (argv / second-instance). Only a login ticket for an
   * attempt this process started is redeemable; anything else is dropped. */
  async handleDeepLink(url: string): Promise<void> {
    const ticket = parseLoginTicket(url, this.opts.scheme);
    if (ticket === null) {
      this.log.warn("DEEP LINK dropped: not a login ticket for this scheme");
      return;
    }
    const pending = this.pendingSignIn;
    if (pending === null) {
      // The hijack shape: an unsolicited ticket with no nonce to redeem it.
      this.log.warn("DEEP LINK dropped: login ticket with no sign-in attempt in flight (unsolicited)");
      return;
    }
    clearTimeout(pending.timer);
    this.pendingSignIn = null; // one attempt per nonce, ever
    this.log.info("DEEP LINK: login ticket received — exchanging (ticket + nonce never logged)");
    const outcome = await this.auth.exchangeTicket(ticket, pending.nonce);
    if (!outcome.ok) {
      const why = outcome.kind === "network" ? `network: ${outcome.message}` : `${outcome.status} ${outcome.code}`;
      this.log.warn(`TICKET EXCHANGE FAILED (${why}) — signed-out view`);
      this.surface.showView("signed-out", "Sign-in could not be completed. Try again.");
      return;
    }
    await this.adopt(outcome.tokens, "sign-in");
    this.log.info(`SIGNED IN as ${outcome.tokens.userEmail ?? "(unknown)"} — session written to safeStorage and the cookie jar`);
    this.startRotation();
    await this.surface.loadPage();
  }

  // ── rotation ───────────────────────────────────────────────────────────────
  startRotation(): void {
    this.stopRotation();
    this.stopped = false;
    this.log.info(`Rotation timer started: every ${this.opts.refreshMinutes} min (access TTL ${this.opts.accessTtlMinutes ?? 60} min)`);
    this.scheduleRotation(this.opts.refreshMinutes * 60_000);
  }

  stopRotation(): void {
    if (this.rotationTimer) clearTimeout(this.rotationTimer);
    this.rotationTimer = null;
  }

  private scheduleRotation(delayMs: number): void {
    if (this.rotationTimer) clearTimeout(this.rotationTimer);
    this.rotationTimer = setTimeout(() => {
      void (async () => {
        if (this.stopped || this.tokens === null) return;
        const ok = await this.rotate("timer");
        if (this.stopped || this.tokens === null) return; // rejected → cleared
        if (ok) this.scheduleRotation(this.opts.refreshMinutes * 60_000);
        else {
          this.log.warn(`Rotation deferred by a network error; retrying in ${NETWORK_RETRY_MS / 1000}s`);
          this.scheduleRotation(NETWORK_RETRY_MS);
        }
      })();
    }, delayMs);
  }

  /** One refresh + store + jar re-write, single-flight. true = rotated;
   * false = network error (tokens kept) OR rejected (tokens cleared — check
   * hasSession to tell the two apart). */
  rotate(reason: string): Promise<boolean> {
    if (this.inFlight) {
      this.log.info(`Rotation already in flight; coalesced (reason=${reason})`);
      return this.inFlight;
    }
    this.inFlight = this.rotateOnce(reason).finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }

  private async rotateOnce(reason: string): Promise<boolean> {
    const current = this.tokens;
    if (current === null) return false;
    const outcome = await this.auth.refresh(current.refreshToken);
    if (outcome.ok) {
      await this.adopt(outcome.tokens, reason);
      this.rotationCount += 1;
      this.log.info(`SESSION ROTATED #${this.rotationCount} (reason=${reason}) — new access+refresh written to safeStorage and the cookie jar`);
      return true;
    }
    if (outcome.kind === "network") {
      this.log.warn(`Rotation failed (network: ${outcome.message}, reason=${reason}) — stored session kept`);
      return false;
    }
    this.log.warn(`SESSION REFRESH REJECTED (${outcome.status} ${outcome.code}, reason=${reason}) — clearing the stored session`);
    await this.clearLocal();
    if (reason === "timer") this.surface.showView("signed-out", "Your session ended. Sign in again.");
    return false;
  }

  private async adopt(tokens: SessionTokens, reason: string): Promise<void> {
    this.tokens = tokens;
    this.store.save(tokens);
    await this.injectCookies(tokens, reason);
  }

  // ── cookies: the ONLY way session state reaches the page ───────────────────
  private async injectCookies(tokens: SessionTokens, reason: string): Promise<void> {
    const secure = this.opts.gatewayUrl.startsWith("https:");
    const common = { url: this.opts.gatewayUrl, path: "/", httpOnly: true, secure, sameSite: "lax" as const };
    // Host-only: no `domain` → Electron/Chromium sets a host-only cookie for
    // the URL's host, exactly what the server does (auth_cookie_domain="").
    // SESSION cookies (no expirationDate): they are not persisted to disk, so
    // the jar cannot outlive safeStorage — it is rebuilt from the store at
    // every boot. (Found live: with an expirationDate the jar survived a
    // restart and a page could be signed in while the host had no session.)
    await this.cookies.set({ ...common, name: ACCESS_COOKIE, value: tokens.accessToken });
    await this.cookies.set({ ...common, name: REFRESH_COOKIE, value: tokens.refreshToken });
    await this.cookies.flushStore();
    const jar = await this.cookies.get({ url: this.opts.gatewayUrl });
    const access = jar.filter((c) => c.name === ACCESS_COOKIE);
    const refresh = jar.filter((c) => c.name === REFRESH_COOKIE);
    const describe = (c: Electron.Cookie) =>
      `domain=${c.domain} hostOnly=${c.hostOnly} path=${c.path} httpOnly=${c.httpOnly} secure=${c.secure} sameSite=${c.sameSite} sha256/8=${fingerprint(c.value)}`;
    this.log.info(`Jar updated (reason=${reason}): ${access.length} access cookie(s), ${refresh.length} refresh cookie(s) for ${this.opts.gatewayUrl}`);
    for (const c of access) this.log.info(`  ${ACCESS_COOKIE}: ${describe(c)}`);
    for (const c of refresh) this.log.info(`  ${REFRESH_COOKIE}: ${describe(c)}`);
  }

  private async clearJar(): Promise<void> {
    await this.cookies.remove(this.opts.gatewayUrl, ACCESS_COOKIE);
    await this.cookies.remove(this.opts.gatewayUrl, REFRESH_COOKIE);
    await this.cookies.flushStore();
    const left = (await this.cookies.get({ url: this.opts.gatewayUrl })).filter((c) => c.name === ACCESS_COOKIE || c.name === REFRESH_COOKIE).length;
    this.log.info(`Jar cleared (${left} session cookie(s) remain)`);
  }

  private async clearLocal(): Promise<void> {
    this.tokens = null;
    this.stopRotation();
    this.store.clear();
    await this.clearJar();
  }

  // ── the portal redirect: re-auth signal, or logout intent ──────────────────
  /** index.ts calls this when the page's own POST /portal/auth/logout completes. */
  markLogoutIntent(): void {
    this.logoutIntentAt = Date.now();
    this.log.info("LOGOUT INTENT observed: the page revoked its session (POST /portal/auth/logout 200); the next portal bounce is a logout, not a re-auth");
  }

  /** The navigation guard already refused it; this decides what it meant. */
  async onPortalRedirect(url: string): Promise<void> {
    if (Date.now() - this.logoutIntentAt < LOGOUT_INTENT_WINDOW_MS) {
      this.logoutIntentAt = 0;
      this.log.info(`PORTAL REDIRECT = LOGOUT (page-initiated): ${url}`);
      await this.signOut("page-logout", false); // server already revoked it
      return;
    }
    if (this.tokens === null) {
      this.log.info("PORTAL REDIRECT with no session — ignored (signed-out view is in charge)");
      return;
    }
    if (this.inFlight) {
      this.log.info(`Portal redirect while a re-auth is already in flight; coalesced (${url})`);
      await this.inFlight;
      return;
    }
    if (this.tripsLoopGuard()) {
      this.log.error(`RE-AUTH LOOP: ${LOOP_GUARD_MAX} portal redirects within ${LOOP_GUARD_WINDOW_MS / 1000}s despite successful refreshes — giving up to the signed-out view`);
      await this.clearLocal();
      await this.surface.blankPage();
      this.surface.showView("signed-out", "The session could not be repaired. Sign in again.");
      return;
    }
    this.log.info(`PORTAL REDIRECT INTERCEPTED: ${url} — raising host-side re-auth`);
    const ok = await this.rotate("portal-redirect");
    if (ok) {
      this.log.info("Re-auth via refresh succeeded; reloading the page");
      this.surface.reloadPage();
      return;
    }
    if (this.tokens === null) {
      this.log.warn("Refresh token rejected — falling back to the interactive sign-in");
      await this.surface.blankPage();
      await this.startSignIn();
    } else {
      this.log.warn("Re-auth deferred by a network error; the page stays as it is");
    }
  }

  private tripsLoopGuard(): boolean {
    const now = Date.now();
    this.recentRedirects = this.recentRedirects.filter((t) => now - t <= LOOP_GUARD_WINDOW_MS);
    this.recentRedirects.push(now);
    return this.recentRedirects.length >= LOOP_GUARD_MAX;
  }

  // ── logout ─────────────────────────────────────────────────────────────────
  async signOut(reason: string, revokeOnServer: boolean): Promise<void> {
    this.stopped = true;
    this.cancelSignIn(false);
    const refresh = this.tokens?.refreshToken ?? null;
    if (revokeOnServer && refresh !== null) {
      const r = await this.auth.logout(refresh);
      this.log.info(`Server-side revocation ${r.ok ? "ok" : "FAILED"} (status ${r.status})`);
    }
    await this.clearLocal();
    await this.surface.blankPage();
    this.surface.showView("signed-out");
    this.log.info(`SIGNED OUT (reason=${reason}): safeStorage cleared, jar cleared, page blanked`);
  }

  // ── probes (dev only; index.ts gates them on !app.isPackaged) ──────────────
  async probeInjectFromFile(json: { access_token: string; refresh_token: string; user?: { email?: string } }): Promise<void> {
    const tokens: SessionTokens = { accessToken: json.access_token, refreshToken: json.refresh_token, userEmail: json.user?.email ?? null };
    this.store.save(tokens);
    this.log.info(`PROBE: session written to safeStorage from a file (refresh sha256/8=${fingerprint(tokens.refreshToken)}); booting as a warm start`);
  }

  async probeCorruptAccessCookie(): Promise<void> {
    const before = (await this.cookies.get({ url: this.opts.gatewayUrl, name: ACCESS_COOKIE }))[0];
    const garbage = "corrupt." + randomBytes(24).toString("base64url");
    await this.cookies.set({ url: this.opts.gatewayUrl, name: ACCESS_COOKIE, value: garbage, path: "/", httpOnly: true, sameSite: "lax", expirationDate: Math.floor(Date.now() / 1000) + 3600 });
    await this.cookies.flushStore();
    const after = (await this.cookies.get({ url: this.opts.gatewayUrl, name: ACCESS_COOKIE }))[0];
    this.log.info(`PROBE: access cookie corrupted in the jar (sha256/8 ${before ? fingerprint(before.value) : "-"} → ${after ? fingerprint(after.value) : "-"}); refresh cookie untouched; reloading`);
    this.surface.reloadPage();
  }
}
