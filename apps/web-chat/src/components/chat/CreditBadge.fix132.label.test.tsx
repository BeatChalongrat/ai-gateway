// FIX132 F21 — the credits pill showed a bare number: NIGHT3 j1-02 read
// "93,819,763" with no label or unit (the words existed only as the
// aria-label, with no tooltip). Now the pill says what the number is —
// "เครดิต" after it — and the hover title gives the full sentence.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const usage = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock("@/hooks/useUsage", () => ({ useUsage: () => usage.state }));
vi.mock("@/stores/modelStore", () => ({ useModelStore: (sel: (s: { models: unknown[] }) => unknown) => sel({ models: [] }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import { CreditBadge } from "./CreditBadge";

const th = dictionaries.th;
beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  usage.state = { data: { credits: { total: 100_000_000, used: 6_180_237, remaining: 93_819_763, reset_date: null }, usage_by_model: [], total_credits_used: 0, period: "lifetime" }, isError: false };
});
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 F21 — the credit number has a label", () => {
  it("the pill reads '93,819,763 เครดิต' and its title says remaining of total", () => {
    render(<CreditBadge />);
    const pill = screen.getByRole("button", { name: th["a11y.usageAndCredits"] });
    expect(pill).toHaveTextContent("93,819,763");
    expect(pill).toHaveTextContent(th["usage.pill.unit"]);
    expect(th["usage.pill.unit"]).toBe("เครดิต");
    expect(pill.getAttribute("title")).toContain("93,819,763");
    expect(pill.getAttribute("title")).toContain("100,000,000");
  });
});
