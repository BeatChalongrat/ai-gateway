// FIX43 Part B4 — /chat/settings has a web half and a desktop half, and the
// device rows never render in a browser, disabled or otherwise.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ hosted: false }));

vi.mock("@/lib/auth", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  get HOSTED_MODE() {
    return state.hosted;
  },
  logoutSession: vi.fn(),
}));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => state.hosted }));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "somchai@acme.ai.gateway", user_id: "u1" } }) }));
vi.mock("@/lib/hostApi", () => ({
  hostApi: {
    getSettings: vi.fn(async () => ({
      theme: "Dark", minimizeToTray: true, startWithWindows: false, sendCrashReports: false,
      backupAgentConversations: true, preferLocalOffice: false, grantedFolders: [], gatewayUrl: "", chatEdgeUrl: "",
    })),
    listFolders: vi.fn(async () => []),
    getShellPrefs: vi.fn(async () => ({ ok: true, data: { quickAskHotkey: "" } })),
    listSites: vi.fn(async () => ({ ok: true, data: [] })),
    getBrowserBridgeStatus: vi.fn(async () => ({ ok: false })),
    browserStatus: vi.fn(async () => ({ ok: false })),
  },
}));

import SettingsPage from "./page";

afterEach(cleanup);
beforeEach(() => {
  state.hosted = false;
  localStorage.clear();
});

describe("FIX43 — /chat/settings per surface", () => {
  it("web: appearance + account + data, and not one device row", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByTestId("web-settings")).toBeTruthy());
    expect(screen.getByTestId("web-settings-theme")).toBeTruthy();
    expect(screen.getByTestId("web-settings-language")).toBeTruthy();
    expect(screen.getByTestId("web-settings-email").textContent).toContain("somchai@acme.ai.gateway");
    // The doors to account-wide pages that exist on the web.
    expect(document.querySelector('a[href="/chat/memory"]')).toBeTruthy();
    expect(document.querySelector('a[href="/chat/knowledge"]')).toBeTruthy();
    expect(document.querySelector('a[href="/chat/skills"]')).toBeTruthy();
    // No door to rooms that do not exist on the web.
    expect(document.querySelector('a[href="/chat/connectors"]')).toBeNull();
    expect(document.querySelector('a[href="/chat/writing-style"]')).toBeNull();
    // The device rows: absent, not disabled.
    const text = document.body.textContent ?? "";
    for (const s of ["Minimize to tray", "Start with Windows", "Folder access", "Approved sites", "Send crash reports"]) {
      expect(text).not.toContain(s);
    }
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
  });

  it("desktop: the device page, with its switches", async () => {
    state.hosted = true;
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getAllByRole("switch").length).toBeGreaterThan(0));
    expect(screen.queryByTestId("web-settings")).toBeNull();
    expect(document.body.textContent).toContain("Minimize to tray");
  });
});
