// Phase-3 i18n cleanup render tests — plan card + login page under
// both locales, per the phase's acceptance criteria. The runtime
// parity test in i18n.sweep.test.tsx already validates the new keys'
// EN/TH parity automatically.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { executeExcelPlan: vi.fn(), downloadFile: vi.fn() },
  ApiError: class extends Error {},
}));

import LoginPage from "@/app/login/page";
import { ExcelPlanCard } from "@/components/chat/ExcelPlanCard";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

afterEach(cleanup);
beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});

const plan = {
  operation: "create_workbook",
  output_filename: "report.xlsx",
  actions: [],
};

describe("plan card locale rendering", () => {
  it("ExcelPlanCard renders the EN execute label in en", () => {
    render(<ExcelPlanCard convId="c1" plan={plan} />);
    expect(screen.getByText(dictionaries.en["plan.execute"])).toBeTruthy();
  });

  it("ExcelPlanCard renders the TH execute label in th, no English leak", () => {
    useI18nStore.setState({ locale: "th" });
    render(<ExcelPlanCard convId="c1" plan={plan} />);
    expect(screen.getByText(dictionaries.th["plan.execute"])).toBeTruthy();
    expect(screen.queryByText(dictionaries.en["plan.execute"])).toBeNull();
  });
});

describe("login page locale rendering", () => {
  it("renders the redirect notice per locale", () => {
    render(<LoginPage />);
    expect(
      screen.getByText(dictionaries.en["auth.redirectSignin"]),
    ).toBeTruthy();
    cleanup();

    useI18nStore.setState({ locale: "th" });
    render(<LoginPage />);
    expect(
      screen.getByText(dictionaries.th["auth.redirectSignin"]),
    ).toBeTruthy();
  });
});
