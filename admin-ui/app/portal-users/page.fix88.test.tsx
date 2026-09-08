// FIX88 Part A — credits an admin can actually manage. The page shows the
// balance against a stored CEILING (credit_quota), offers the three
// operations the owner described, and never invents its own arithmetic:
// the preview it shows is the same rule the backend applies.
//
// The owner's four rows, as the admin sees them:
//   1. 500k left of 1M, top up 500k        → 1,000,000 / 1,000,000
//   2. anything of 1M, reset to full       → 1,000,000 / 1,000,000
//   3. full 1M, ceiling → 2M               → 2,000,000 / 2,000,000
//   4. 500k of 1M, ceiling → 2M            → 1,500,000 / 2,000,000

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("../../lib/api", () => ({
  api: { get, post },
  ApiError: class ApiError extends Error {
    body: unknown;
    constructor(message: string, body?: unknown) {
      super(message);
      this.body = body;
    }
  },
}));

const user = (over: Record<string, unknown>) => ({
  id: "u1",
  email: "somchai@example.com",
  display_name: "Somchai",
  plan: "free_trial",
  status: "active",
  credit_balance: 500_000,
  credit_used: 500_000,
  credit_quota: 1_000_000,
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

// ONE stable object per render pass (a fresh object per render re-runs the
// page's effects forever — the FIX85 lesson).
let USERS = { data: { users: [user({})], total: 1 }, loading: false, error: null, refetch: vi.fn() };
vi.mock("../../lib/useApi", () => ({ useApi: () => USERS }));

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";
import { balanceAfterCeiling, roomToCeiling } from "../../lib/credits";
import PortalUsersPage from "./page";

function mount(users: ReturnType<typeof user>[]) {
  USERS = { data: { users, total: users.length }, loading: false, error: null, refetch: vi.fn() };
  return render(
    <I18nProvider>
      <ToastProvider>
        <PortalUsersPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

/** Open the row menu of the Nth row and click one of its entries. */
function rowAction(index: number, label: RegExp) {
  const rows = screen.getAllByTestId("user-row");
  fireEvent.click(within(rows[index]!).getByLabelText("Actions"));
  fireEvent.click(screen.getByText(label));
}

beforeEach(() => {
  vi.clearAllMocks();
  get.mockResolvedValue({ ...user({}), transactions: [] });
  post.mockResolvedValue({ user: user({}), transaction: null });
  localStorage.setItem("sran-admin-locale", "en");
});
afterEach(cleanup);

describe("the arithmetic is one rule, and it reproduces the owner's table", () => {
  it("balanceAfterCeiling: raising lands a full account full again, spent stays spent, lowering floors at 0", () => {
    expect(balanceAfterCeiling(1_000_000, 1_000_000, 2_000_000)).toBe(2_000_000); // row 3
    expect(balanceAfterCeiling(500_000, 1_000_000, 2_000_000)).toBe(1_500_000); // row 4
    expect(balanceAfterCeiling(300_000, 1_000_000, 500_000)).toBe(0); // −500k from 300k
    expect(balanceAfterCeiling(0, 1_000_000, 100_000)).toBe(0);
    expect(roomToCeiling(500_000, 1_000_000)).toBe(500_000);
    expect(roomToCeiling(1_200_000, 1_000_000)).toBe(0);
  });
});

describe("the table reads balance / ceiling", () => {
  it("a row shows both numbers and the detail drawer names the ceiling", async () => {
    mount([user({})]);
    expect(screen.getByTestId("row-credits").textContent).toBe("500K / 1M");
    fireEvent.click(screen.getAllByTestId("user-row")[0]!);
    await waitFor(() => expect(screen.getByTestId("detail-quota")).toBeTruthy());
    expect(screen.getByTestId("detail-balance").textContent).toBe("500,000");
    expect(screen.getByTestId("detail-quota").textContent).toBe("1,000,000");
  });
});

describe("row 1 — a top-up is capped at the ceiling", () => {
  it("500k on 500k-of-1M is allowed and posts amount 500,000", async () => {
    mount([user({})]);
    rowAction(0, /^Top-up Credits$/);
    fireEvent.change(screen.getByTestId("amount-input"), { target: { value: "500000" } });
    expect(screen.queryByTestId("over-ceiling")).toBeNull();
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/admin/portal-users/u1/topup", { amount: 500_000, description: undefined });
  });

  it("600k on the same account is refused BEFORE any request, naming the room left", () => {
    mount([user({})]);
    rowAction(0, /^Top-up Credits$/);
    fireEvent.change(screen.getByTestId("amount-input"), { target: { value: "600000" } });
    const warning = screen.getByTestId("over-ceiling");
    expect(warning.textContent).toContain("100,000"); // the overshoot
    expect(warning.textContent).toContain("500,000"); // the room left
    expect((screen.getByTestId("credit-action-primary") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    expect(post).not.toHaveBeenCalled();
  });

  it("'fill to full' is offered with the gap, and posts fill:true", async () => {
    mount([user({})]);
    rowAction(0, /^Top-up Credits$/);
    const fill = screen.getByTestId("fill-to-full");
    expect(fill.textContent).toContain("500K");
    fireEvent.click(fill);
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/admin/portal-users/u1/topup", { fill: true, description: undefined });
  });

  it("an account already at its ceiling cannot be filled", () => {
    mount([user({ credit_balance: 1_000_000 })]);
    rowAction(0, /^Top-up Credits$/);
    const fill = screen.getByTestId("fill-to-full") as HTMLButtonElement;
    expect(fill.disabled).toBe(true);
    expect(fill.textContent).toContain("Already at the ceiling");
  });
});

describe("row 2 — reset to full", () => {
  it("names the movement and posts to /reset", async () => {
    post.mockResolvedValue({ user: user({ credit_balance: 1_000_000 }), transaction: { id: "t", amount: 500_000, balance_after: 1_000_000, type: "reset_to_full", description: null, created_by: "admin", created_at: "x" } });
    mount([user({})]);
    rowAction(0, /^Reset to full$/);
    const preview = screen.getByTestId("reset-preview").textContent ?? "";
    expect(preview).toContain("500,000");
    expect(preview).toContain("1,000,000");
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/admin/portal-users/u1/reset", { description: undefined }));
  });

  it("an already-full account says so instead of posting a zero", () => {
    mount([user({ credit_balance: 1_000_000 })]);
    rowAction(0, /^Reset to full$/);
    expect(screen.getByTestId("reset-preview").textContent).toContain("Already at the ceiling");
  });
});

describe("rows 3 and 4 — changing the ceiling moves the balance by the same difference", () => {
  it("full 1M → ceiling 2M previews 2,000,000 / 2,000,000 and posts the quota", async () => {
    mount([user({ credit_balance: 1_000_000, credit_used: 0 })]);
    rowAction(0, /^Change ceiling$/);
    fireEvent.change(screen.getByTestId("quota-input"), { target: { value: "2000000" } });
    const preview = screen.getByTestId("quota-preview").textContent ?? "";
    expect(preview).toContain("1,000,000 → 2,000,000");
    expect(preview).toContain("balance 1,000,000 → 2,000,000");
    expect(screen.queryByTestId("lower-warning")).toBeNull();
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/admin/portal-users/u1/quota", { quota: 2_000_000, description: undefined }));
  });

  it("500k of 1M → ceiling 2M previews 1,500,000 / 2,000,000 (the spent 500k stays spent)", () => {
    mount([user({})]);
    rowAction(0, /^Change ceiling$/);
    fireEvent.change(screen.getByTestId("quota-input"), { target: { value: "2000000" } });
    expect(screen.getByTestId("quota-preview").textContent).toContain("balance 500,000 → 1,500,000");
  });

  it("lowering is destructive: a confirm names the user, the new ceiling and the new balance", async () => {
    mount([user({})]);
    rowAction(0, /^Change ceiling$/);
    // 1M → 800k on a 500k balance: −200,000, the balance lands at 300,000.
    fireEvent.change(screen.getByTestId("quota-input"), { target: { value: "800000" } });
    expect(screen.getByTestId("lower-warning")).toBeTruthy();
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    // Nothing posted yet — the confirm stands between.
    expect(post).not.toHaveBeenCalled();
    await screen.findByText(/Lower the ceiling\?/);
    const text = document.body.textContent ?? "";
    expect(text).toContain("Somchai");
    expect(text).toContain("1,000,000 → 800,000");
    expect(text).toContain("500,000 → 300,000");
    expect(text).toContain("removes 200,000");
    fireEvent.click(screen.getByText("Lower ceiling"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/admin/portal-users/u1/quota", { quota: 800_000, description: undefined }));
  });
});

describe("bulk — explicit selection, one confirm naming the count and the total", () => {
  const three = [
    user({ id: "a", display_name: "A", email: "a@x.com", credit_balance: 500_000, credit_quota: 1_000_000 }),
    user({ id: "b", display_name: "B", email: "b@x.com", credit_balance: 900_000, credit_quota: 1_000_000 }),
    user({ id: "c", display_name: "C", email: "c@x.com", credit_balance: 0, credit_quota: 1_000_000 }),
  ];

  it("no bulk bar until rows are ticked; the bar counts the selection", () => {
    mount(three);
    expect(screen.queryByTestId("bulk-bar")).toBeNull();
    const boxes = screen.getAllByTestId("select-row");
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[2]!);
    expect(screen.getByTestId("bulk-count").textContent).toBe("2 selected");
  });

  it("a bulk top-up cannot fire without the confirm, which names the users, the amount and the total", async () => {
    post.mockResolvedValue({
      results: [
        { user_id: "a", ok: true, balance_after: 1_000_000, quota_after: 1_000_000, moved: 500_000 },
        { user_id: "c", ok: true, balance_after: 500_000, quota_after: 1_000_000, moved: 500_000 },
      ],
      total_moved: 1_000_000,
      reduced_count: 0,
    });
    mount(three);
    const boxes = screen.getAllByTestId("select-row");
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[2]!);
    fireEvent.click(screen.getByTestId("bulk-topup"));
    fireEvent.change(screen.getByTestId("amount-input"), { target: { value: "500000" } });
    expect(screen.getByTestId("bulk-preview").textContent).toContain("1,000,000");
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    expect(post).not.toHaveBeenCalled(); // the confirm stands between
    await screen.findByText("Apply to 2 users?");
    const text = document.body.textContent ?? "";
    expect(text).toContain("Top up 2 users by 500,000 each");
    expect(text).toContain("1,000,000 credits in total");
    fireEvent.click(screen.getByText("Apply to 2 users"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/admin/portal-users/bulk", {
      user_ids: ["a", "c"],
      description: undefined,
      action: "topup",
      amount: 500_000,
    });
  });

  it("the confirm says how many of the selection the ceiling would refuse", async () => {
    mount(three);
    screen.getAllByTestId("select-row").forEach((b) => fireEvent.click(b));
    fireEvent.click(screen.getByTestId("bulk-topup"));
    fireEvent.change(screen.getByTestId("amount-input"), { target: { value: "500000" } });
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    await screen.findByText("Apply to 3 users?");
    // B has only 100k of room → skipped; the total counts the other two.
    const text = document.body.textContent ?? "";
    expect(text).toContain("1,000,000 credits in total");
    expect(text).toContain("1 of them would pass their ceiling");
  });

  it("a bulk ceiling change calls out the reductions separately", async () => {
    mount(three);
    screen.getAllByTestId("select-row").forEach((b) => fireEvent.click(b));
    fireEvent.click(screen.getByTestId("bulk-quota"));
    fireEvent.change(screen.getByTestId("quota-input"), { target: { value: "600000" } });
    expect(screen.getByTestId("lower-warning")).toBeTruthy();
    fireEvent.click(screen.getByTestId("credit-action-primary"));
    await screen.findByText("Apply to 3 users?");
    const text = document.body.textContent ?? "";
    // A: 500k→100k, B: 900k→500k, C: 0 (floored) → −800,000 moved, 3 reductions
    expect(text).toContain("-800,000");
    expect(text).toContain("3 of them are reductions");
  });

  it("'select all listed' selects only what the filter shows, and Clear empties it", () => {
    mount(three);
    fireEvent.click(screen.getByTestId("select-all"));
    expect(screen.getByTestId("bulk-count").textContent).toBe("3 selected");
    fireEvent.click(screen.getByTestId("bulk-clear"));
    expect(screen.queryByTestId("bulk-bar")).toBeNull();
  });
});
