// AM11 (B, C) — on the Agent Market page of the admin app: a problem report says its round's status
// in words (the walk read "สถานะรอบ succeeded (—)"), never prints the reason code of a round that
// stopped, and the Thai screen calls the admin's log by the name the sidebar gives it.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { am } = vi.hoisted(() => ({ am: { problems: vi.fn(), closeProblem: vi.fn() } }));
vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: am };
});
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/agent-market",
}));

import { I18nProvider } from "../../lib/i18n";
import { dictionaries } from "../../lib/i18n/dict";
import ProblemsSection from "./_sections/Problems";

const inLanguage = (locale: "en" | "th", el: React.ReactNode) => {
  localStorage.setItem("sran-admin-locale", locale);
  return render(<I18nProvider>{el}</I18nProvider>);
};

// the stored code as the walk read it: in lower case, a word of its own
const asAWord = (code: string) => new RegExp(`(^|[^A-Za-z_])${code}([^A-Za-z_]|$)`);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});
afterEach(cleanup);

describe("a problem report", () => {
  const problem = {
    id: "p1", ref: "12", email: "a@example.test", text: "what happened", status: "open", created_at: "2026-10-05T10:00:00Z",
    slug: "sran.invoice-po-matching", version: "1.0.1", run_id: "59d64ac3-e519-4b7b-9d57-000000000000",
    run_status: "succeeded", run_reason: "", step: "publish", step_error: "", page: "/chat/agents/runs/59d64ac3",
    closed_by: "", closed_at: null, close_note: "",
  };

  it.each([
    ["th", "รอบนี้: เสร็จแล้ว"],
    ["en", "This run: Finished"],
  ] as const)("says the round's status in words (%s)", async (locale, line) => {
    am.problems.mockResolvedValue({ problems: [problem] });
    inLanguage(locale, <ProblemsSection admin />);
    const row = await screen.findByTestId("am-problem");
    await waitFor(() => expect(row.textContent).toContain(line));
    expect(row.textContent).not.toMatch(asAWord("succeeded"));
  });

  it("a round that stopped for a reason shows no reason code", async () => {
    am.problems.mockResolvedValue({ problems: [{ ...problem, run_status: "failed", run_reason: "render_failed" }] });
    inLanguage("th", <ProblemsSection admin />);
    const row = await screen.findByTestId("am-problem");
    await waitFor(() => expect(row.textContent).toContain("รอบนี้: ไม่สำเร็จ"));
    expect(row.textContent).not.toContain("render_failed");
    expect(row.getAttribute("data-run-reason")).toBe("render_failed");
  });
});

describe("the admin's log has one name on the Thai screen", () => {
  const th = dictionaries.th as Record<string, string>;
  it("the accounts page and the models section call it what the sidebar does", () => {
    expect(th["acct.subtitle"]).toContain(th["nav.auditLog"]);
    expect(th["am.models.auditLink"]).toBe(th["nav.auditLog"]);
    const holding = Object.entries(th).filter(([, v]) => v.includes("audit log")).map(([k]) => k);
    expect(holding).toEqual([]);
  });
});
