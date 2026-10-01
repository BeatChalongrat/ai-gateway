// AM9 (E) — granting a role is a screen, not SQL; an agent publisher's first
// screen is the Agent Market page, not a dashboard of errors.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api, replace } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() },
  replace: vi.fn(),
}));
vi.mock("../../lib/api", async () => {
  const real = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...real, api };
});
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

import { I18nProvider } from "../../lib/i18n";
import { ApiError } from "../../lib/api";
import { ROLE_NAV, landingFor } from "../../lib/auth";
import DashboardPage from "../page";
import AccountsPage from "./page";

const wrap = (el: React.ReactNode) => render(<I18nProvider>{el}</I18nProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});
afterEach(cleanup);

describe("where each role lands", () => {
  it("an agent publisher lands on the Agent Market page; admins on the dashboard; super_admin has a nav", () => {
    expect(landingFor("agent_publisher")).toBe("/agent-market");
    expect(landingFor("admin")).toBe("/");
    expect(landingFor("super_admin")).toBe("/");
    expect(ROLE_NAV.super_admin).toContain("/accounts");
    expect(ROLE_NAV.agent_publisher).not.toContain("/");
  });

  it("the dashboard sends an agent publisher to the Agent Market page and asks the gateway for nothing", async () => {
    localStorage.setItem("admin_session_cache", JSON.stringify({ role: "agent_publisher", name: "lead", email: "lead@example.test" }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ user: { role: "agent_publisher", email: "lead@example.test" } }) }));
    const { container } = wrap(<DashboardPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/agent-market"));
    expect(api.get).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
    vi.unstubAllGlobals();
  });
});

describe("the accounts screen", () => {
  const listing = {
    me: "u-admin",
    roles: ["admin", "tenant_admin", "project_admin", "agent_publisher", "viewer"],
    accounts: [
      { id: "u-admin", username: "it", email: "it@example.test", role: "admin", is_active: true, created_at: null, updated_at: null },
      { id: "u-lead", username: "lead", email: "lead@example.test", role: "viewer", is_active: true, created_at: null, updated_at: null },
    ],
  };

  it("an admin gives the finance lead agent_publisher; their own role cannot be changed here", async () => {
    api.get.mockResolvedValue(listing);
    api.patch.mockResolvedValue({ account: { ...listing.accounts[1], role: "agent_publisher" }, sessions_ended: 0 });
    wrap(<AccountsPage />);
    const lead = (await screen.findByTestId("acct-role-lead@example.test")) as HTMLSelectElement;
    expect((screen.getByTestId("acct-role-it@example.test") as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(lead, { target: { value: "agent_publisher" } });
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/admin/accounts/u-lead", { role: "agent_publisher" }));
  });

  it("a new password is typed in a masked field, never a prompt", async () => {
    api.get.mockResolvedValue(listing);
    api.post.mockResolvedValue({ account: listing.accounts[1], sessions_ended: 1 });
    const prompt = vi.spyOn(window, "prompt");
    wrap(<AccountsPage />);
    fireEvent.click((await screen.findAllByText(/Set a new password|ตั้งรหัสผ่านใหม่/))[1]);
    const field = screen.getByTestId("acct-pw-lead@example.test") as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.change(field, { target: { value: "a-long-passphrase-1" } });
    fireEvent.click(screen.getByTestId("acct-pw-save-lead@example.test"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/accounts/u-lead/password", { password: "a-long-passphrase-1" }));
    expect(prompt).not.toHaveBeenCalled();
  });

  it("an account that is not an admin is told so", async () => {
    api.get.mockRejectedValue(new ApiError(403, { error: { message: "no" } }));
    wrap(<AccountsPage />);
    expect(await screen.findByTestId("acct-denied")).toBeTruthy();
  });
});
