// FIX122 — the re-auth loop guard gives up WITHOUT wiping a session whose
// refreshes succeeded. A page that keeps bouncing to the portal (FIX122: a
// page origin the gateway's CORS refuses) is not a broken session; wiping
// it forced the owner to sign in again.
import { describe, expect, it, vi } from "vitest";
import type { Cookies } from "electron";
import { SessionController, type SessionSurface } from "../src/main/session-controller";
import type { PortalAuthClient, SessionTokens } from "../src/main/portal-auth";
import type { SessionStore } from "../src/main/session-store";
import type { Logger } from "../src/main/logger";

function harness() {
  let n = 0;
  let saved: SessionTokens | null = { accessToken: "a0", refreshToken: "r0", userEmail: "owner@local" };
  const store = {
    load: vi.fn(() => (saved ? { ...saved, savedAt: "" } : null)),
    save: vi.fn((t: SessionTokens) => { saved = t; return { ...t, savedAt: "" }; }),
    clear: vi.fn(() => { saved = null; }),
    exists: vi.fn(() => saved !== null),
  };
  const auth = {
    refresh: vi.fn(async () => {
      n += 1;
      return { ok: true as const, tokens: { accessToken: `a${n}`, refreshToken: `r${n}`, userEmail: "owner@local" } };
    }),
  };
  const cookies = {
    set: vi.fn(async () => {}),
    remove: vi.fn(async () => {}),
    get: vi.fn(async () => []),
    flushStore: vi.fn(async () => {}),
  };
  const surface: SessionSurface = {
    showView: vi.fn(),
    loadPage: vi.fn(async () => {}),
    reloadPage: vi.fn(),
    blankPage: vi.fn(async () => {}),
    openExternal: vi.fn(async () => {}),
  };
  const log = { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() };
  const ctl = new SessionController(
    { gatewayUrl: "http://localhost:8080", oauthProvider: "google", oauthClient: "desktop", scheme: "sran-dev", refreshMinutes: 10, signInTimeoutSeconds: 300 },
    store as unknown as SessionStore,
    cookies as unknown as Cookies,
    auth as unknown as PortalAuthClient,
    surface,
    log as unknown as Logger,
  );
  return { ctl, store, auth, surface, saved: () => saved };
}

describe("FIX122 — the re-auth loop guard keeps a session that refreshes", () => {
  it("three portal bounces with successful refreshes stop the loop but keep the stored session", async () => {
    const h = harness();
    expect(await h.ctl.ensureSessionAtBoot()).toBe(true);
    await h.ctl.onPortalRedirect("http://localhost:3001/login");
    await h.ctl.onPortalRedirect("http://localhost:3001/login");
    await h.ctl.onPortalRedirect("http://localhost:3001/login");

    expect(h.store.clear).not.toHaveBeenCalled();
    expect(h.saved()).not.toBeNull();
    expect(h.ctl.hasSession).toBe(true);
    expect(h.surface.blankPage).toHaveBeenCalledTimes(1);
    expect(h.surface.showView).toHaveBeenCalledWith("signed-out", expect.stringContaining("sign-in is kept"));
    ctlStop(h.ctl);
  });

  it("the view's Sign-in restores the kept session without the system browser", async () => {
    const h = harness();
    await h.ctl.ensureSessionAtBoot();
    for (let i = 0; i < 3; i++) await h.ctl.onPortalRedirect("http://localhost:3001/login");
    const refreshes = h.auth.refresh.mock.calls.length;

    await h.ctl.signInFromView();

    expect(h.auth.refresh.mock.calls.length).toBe(refreshes + 1);
    expect(h.surface.loadPage).toHaveBeenCalledTimes(1);
    expect(h.surface.openExternal).not.toHaveBeenCalled();
    expect(h.store.clear).not.toHaveBeenCalled();
    ctlStop(h.ctl);
  });
});

// the rotation timer must not outlive the test
function ctlStop(ctl: SessionController) {
  (ctl as unknown as { stopRotation(): void }).stopRotation();
}
