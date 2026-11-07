// AM7 — the sidebar gives an agent_publisher the dashboard and the Agent
// Market page, nothing else; an admin gets Agent Market beside the rest.
// (The nav is a soft gate: chat-edge enforces every endpoint.)

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/agent-market",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import { I18nProvider } from "../lib/i18n";

import Sidebar from "./Sidebar";

function session(role: string) {
  return { role, name: "Pim", email: "pim@example.invalid" };
}

function serverSays(role: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ user: { role, username: "Pim", email: "pim@example.invalid" } }), { status: 200 })),
  );
}

const hrefs = (root: HTMLElement) => Array.from(root.querySelectorAll("nav a")).map((a) => a.getAttribute("href"));

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Sidebar (AM7)", () => {
  // AM9: the publisher's first screen is the Agent Market page; the dashboard (whose API refuses this role) is not offered
  it("an agent_publisher sees only Agent Market", async () => {
    window.localStorage.setItem("admin_session_cache", JSON.stringify(session("agent_publisher")));
    serverSays("agent_publisher");
    const { container } = render(
      <I18nProvider>
        <Sidebar />
      </I18nProvider>,
    );
    await waitFor(() => expect(hrefs(container)).toEqual(["/agent-market"]));
    // and still so once the session is revalidated with the server
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await waitFor(() => expect(hrefs(container)).toEqual(["/agent-market"]));
    const labels = Array.from(container.querySelectorAll("nav a")).map((a) => a.textContent);
    expect(labels).toEqual(["Agent Market"]);
  });

  it("an admin sees Agent Market among the web chat pages", async () => {
    window.localStorage.setItem("admin_session_cache", JSON.stringify(session("admin")));
    serverSays("admin");
    const { container } = render(
      <I18nProvider>
        <Sidebar />
      </I18nProvider>,
    );
    await waitFor(() => expect(hrefs(container)).toContain("/agent-market"));
    expect(hrefs(container)).toContain("/chat-users");
    expect(hrefs(container).length).toBeGreaterThan(10);
  });
});
