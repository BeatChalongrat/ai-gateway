import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import { useI18nStore } from "@/lib/i18n";
import { OfficeQualityNotice } from "./OfficeQualityNotice";

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

it("does not invent verification for historical results without a report", () => {
  render(<OfficeQualityNotice />);
  expect(screen.queryByRole("status")).toBeNull();
});

it("distinguishes missing calculation from a verified formula value", () => {
  render(<OfficeQualityNotice report={{ filename: "budget.xlsx", status: "partial", recalculated: false, formulas: 5 }} />);
  expect(screen.getByText(/5 formulas have not been recalculated/)).toBeTruthy();
  expect(screen.getByText(/not fully verified/)).toBeTruthy();
});

it("shows rendered pages and recalculation without claiming visual approval", () => {
  render(<OfficeQualityNotice report={{ filename: "budget.xlsx", status: "checked", recalculated: true, formulas: 5, rendered_pages: 2 }} />);
  expect(screen.getByText("Rendered and checked 2 pages.")).toBeTruthy();
  expect(screen.getByText("Recalculated 5 formulas and checked cached results.")).toBeTruthy();
  expect(screen.getByText(/Review the layout and business figures/)).toBeTruthy();
});

it("uses Thai labels", () => {
  useI18nStore.setState({ locale: "th" });
  render(<OfficeQualityNotice report={{ filename: "deck.pptx", status: "checked", recalculated: false, rendered_pages: 3 }} />);
  expect(screen.getByText("เรนเดอร์และตรวจสอบแล้ว 3 หน้า")).toBeTruthy();
});
