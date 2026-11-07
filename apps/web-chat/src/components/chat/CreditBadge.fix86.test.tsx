// FIX86 Parts B and C on the credits badge and its popover:
//  * "used" is ONE number — the ledger — and the request-log roll-up is
//    shown under its own name, never as "used";
//  * when the gateway cannot answer, the badge stays and says so.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const usage = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock("@/hooks/useUsage", () => ({ useUsage: () => usage.state }));
vi.mock("@/stores/modelStore", () => ({ useModelStore: (sel: (s: { models: unknown[] }) => unknown) => sel({ models: [] }) }));

import { CreditBadge } from "./CreditBadge";

const DATA = {
  credits: { total: 100_150_000, used: 47_818_928, remaining: 52_331_072, reset_date: null },
  usage_by_model: [{ model: "gpt-5.6-terra", provider: "openai", tokens: 1, credits_used: 29_326_341, request_count: 1, percentage: 56.6 }],
  // tenant-wide roll-up: a different number, on purpose
  total_credits_used: 51_835_899,
  period: "lifetime",
};

beforeEach(() => {
  usage.state = { data: DATA, isError: false, isStale: false, isFetching: false, refetch: vi.fn(() => Promise.resolve()) };
});
afterEach(cleanup);

describe("credits popover — one number, one source (FIX86 Part B)", () => {
  it("the ring's remaining and the footer's deducted are the SAME ledger, and the roll-up is labelled separately", () => {
    render(<CreditBadge />);
    fireEvent.click(screen.getByRole("button", { name: /Usage and credits|การใช้งานและเครดิต/ }));
    const dialog = screen.getByRole("dialog");
    // ring side: remaining of total
    expect(dialog.textContent).toContain("52,331,072");
    expect(dialog.textContent).toContain("100,150,000");
    // footer: deducted = ledger used, i.e. total − remaining
    expect(screen.getByTestId("usage-deducted").textContent).toContain("47,818,928");
    expect(100_150_000 - 52_331_072).toBe(47_818_928);
    // the roll-up is visible, under its own name — never as the deducted figure
    expect(screen.getByTestId("usage-log-total").textContent).toContain("51,835,899");
    expect(screen.getByTestId("usage-deducted").textContent).not.toContain("51,835,899");
    expect(screen.getByTestId("usage-by-model-scope").textContent).toMatch(/request logs|log การเรียก/);
    expect(dialog.textContent).not.toMatch(/Total used|ใช้ไปทั้งหมด/);
  });
});

describe("credits badge — backend down (FIX86 Part C)", () => {
  it("renders nothing only while the first fetch is in flight", () => {
    usage.state = { data: undefined, isError: false, isStale: false, isFetching: true, refetch: vi.fn() };
    const { container } = render(<CreditBadge />);
    expect(container.querySelector("[data-testid=credit-badge]")).toBeNull();
  });

  it("stays on screen when the fetch failed, says the number is unavailable, and offers a retry", () => {
    const refetch = vi.fn(() => Promise.resolve());
    usage.state = { data: undefined, isError: true, isStale: false, isFetching: false, refetch };
    render(<CreditBadge />);
    const wrap = screen.getByTestId("credit-badge");
    expect(wrap.getAttribute("data-unavailable")).toBe("true");
    const btn = screen.getByRole("button", { name: /Credits unavailable|ยอดเครดิตไม่พร้อมใช้งาน/ });
    expect(btn.textContent).toContain("—");
    expect(btn.textContent).not.toMatch(/\d/);
    fireEvent.click(btn);
    expect(screen.getByTestId("usage-panel-unavailable").textContent).toMatch(/unchanged|ไม่เปลี่ยน/);
    fireEvent.click(screen.getByRole("button", { name: /Retry|ลองใหม่/ }));
    expect(refetch).toHaveBeenCalled();
  });
});
