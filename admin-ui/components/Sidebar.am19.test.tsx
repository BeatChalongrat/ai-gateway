// AM19 — one icon per meaning in the admin nav: "Accounts" (admin sign-ins)
// wore the same two-people glyph as "Portal users"; "Portal settings" wore a
// gear drawn differently from the gear every other settings row uses
// (components/ui/Icon.tsx "settings"). Two faults, opposite: one icon for two
// meanings, one meaning drawn two ways.

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import { I18nProvider } from "../lib/i18n";

import { Icon } from "./ui/Icon";
import Sidebar from "./Sidebar";

function svgOf(root: HTMLElement, href: string): string {
  const a = root.querySelector(`nav a[href="${href}"]`);
  if (!a) throw new Error(`no nav link ${href}`);
  return a.querySelector("svg")?.innerHTML ?? "";
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("admin_session_cache", JSON.stringify({ role: "admin", name: "Pim", email: "pim@example.invalid" }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ user: { role: "admin", username: "Pim", email: "pim@example.invalid" } }), { status: 200 })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Sidebar (AM19) — one icon per meaning", () => {
  it("Accounts and Portal users are two meanings and two glyphs", async () => {
    const { container } = render(
      <I18nProvider>
        <Sidebar />
      </I18nProvider>,
    );
    await waitFor(() => expect(container.querySelector('nav a[href="/accounts"]')).not.toBeNull());
    expect(svgOf(container, "/accounts")).not.toEqual(svgOf(container, "/portal-users"));
    // and Accounts is one person with a cog, Portal users the two people
    expect(svgOf(container, "/accounts")).toContain('cx="18" cy="15"');
    expect(svgOf(container, "/portal-users")).toContain("M23 21v-2a4 4 0 00-3-3.87");
  });

  it("Portal settings wears the same gear as every settings row (ui/Icon 'settings')", async () => {
    const { container } = render(
      <I18nProvider>
        <Sidebar />
      </I18nProvider>,
    );
    await waitFor(() => expect(container.querySelector('nav a[href="/portal-settings"]')).not.toBeNull());
    const gear = render(<Icon name="settings" />).container.querySelector("svg")?.innerHTML ?? "";
    const navGear = svgOf(container, "/portal-settings");
    const d = (s: string) => (s.match(/d="([^"]+)"/)?.[1] ?? "").replace(/\s+/g, " ");
    expect(d(navGear)).toEqual(d(gear));
  });
});
