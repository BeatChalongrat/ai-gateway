// FIX86 Part C — a portal that cannot read the account must not print
// "0 · of 0 · 100 % AVAILABLE" as if it were a true zero (FIX83 row 24).
// Zero and unknown render differently.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", async (orig) => {
  const real = (await orig()) as Record<string, unknown>;
  return { ...real, api: { ...(real.api as object), get: apiMock.get, post: apiMock.post } };
});
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/dashboard" }));
vi.mock("next/link", () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));

import { PortalApiError } from "@/lib/api";
import DashboardPage from "./page";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("dashboard on a 500 (FIX86 Part C)", () => {
  it("prints dashes and the error, never a fabricated zero balance", async () => {
    apiMock.get.mockImplementation(async (path: string) => {
      if (path === "/portal/me") throw new PortalApiError(500, { error: { code: "internal_error", message: "Request failed (500)" } });
      if (path.startsWith("/portal/projects")) return { projects: [] };
      if (path.startsWith("/portal/me/usage")) return { requests: [], total: 0 };
      throw new Error("unexpected " + path);
    });
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByTestId("hero-balance")).toBeTruthy());
    expect(screen.getByTestId("hero-balance").textContent).toBe("—");
    const main = document.body.textContent || "";
    expect(main).toMatch(/Request failed \(500\)/);
    expect(main).toMatch(/could not be loaded/);
    expect(main).not.toMatch(/of 0 credit ceiling/);
    expect(main).not.toMatch(/100%\s*AVAILABLE/i);
    expect(main).not.toMatch(/Free Trial/);
  });

  it("with the account loaded, the hero shows the ledger's numbers", async () => {
    apiMock.get.mockImplementation(async (path: string) => {
      if (path === "/portal/me") return { id: "u", email: "o@x", display_name: "Owner", plan: "free_trial", status: "active", credit_balance: 52331072, credit_used: 47818928, credit_quota: 100150000, credit_percent_used: 47.7, is_low_credit: false, email_verified: true, tenant_id: "t", tenant_role: "owner" };
      if (path.startsWith("/portal/projects")) return { projects: [] };
      if (path.startsWith("/portal/me/usage")) return { requests: [], total: 0 };
      throw new Error("unexpected " + path);
    });
    render(<DashboardPage />);
    await waitFor(() => expect(document.body.textContent).toMatch(/of 100,150,000 credit ceiling/));
    expect(document.body.textContent).toMatch(/47,818,928/);
  });
});

// FIX88 Part A — "of N" is the stored CEILING, so a top-up cannot inflate
// the denominator. Day one after the backfill (quota = balance + used) the
// hero prints exactly what it printed before; a topped-up account then
// reads as full instead of growing its own total.
describe("the hero's total is the ceiling (FIX88 Part A)", () => {
  const me = (over: Record<string, unknown>) => ({
    id: "u", email: "o@x", display_name: "Owner", plan: "free_trial", status: "active",
    credit_percent_used: 0, is_low_credit: false, email_verified: true, tenant_id: "t", tenant_role: "owner",
    ...over,
  });
  const mount = (user: Record<string, unknown>) => {
    apiMock.get.mockImplementation(async (path: string) => {
      if (path === "/portal/me") return user;
      if (path.startsWith("/portal/projects")) return { projects: [] };
      if (path.startsWith("/portal/me/usage")) return { requests: [], total: 0 };
      throw new Error("unexpected " + path);
    });
    render(<DashboardPage />);
  };

  it("a refilled account reads 1,000,000 of 1,000,000 — not of 1,500,000", async () => {
    mount(me({ credit_balance: 1000000, credit_used: 500000, credit_quota: 1000000 }));
    await waitFor(() => expect(document.body.textContent).toMatch(/of 1,000,000 credit ceiling/));
    expect(document.body.textContent).not.toMatch(/1,500,000/);
  });

  it("a gateway that does not send the ceiling keeps the old derivation", async () => {
    mount(me({ credit_balance: 700, credit_used: 300 }));
    await waitFor(() => expect(document.body.textContent).toMatch(/of 1,000 credit ceiling/));
  });
});
