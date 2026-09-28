// AM2 Phase E — a filled template in the outputs card: labelled "แบบฟอร์มของคุณ"
// with its own file type's icon, and its checks listed with the others.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import type { OutputsPayload } from "../types";
import { OutputsCard } from "./OutputsCard";

const outputs: OutputsPayload = {
  run_id: "run-1",
  period_label: "กันยายน 2569",
  artifacts: [
    { id: "a1", name: "ทะเบียนซื้อ_2569-09.xlsx", format: "xlsx", size: 2048, version: 1, quality: { passed: true, checks: [{ name: "xlsx.opens", ok: true, label: "เปิดได้" }] } },
    {
      id: "a2",
      name: "ทะเบียนซื้อ_2569-09_template_v1.xlsx",
      format: "template",
      size: 4096,
      version: 1,
      quality: {
        passed: true,
        checks: [
          { name: "template.filled", ok: true, label: "เติมค่าลงแบบฟอร์มได้", detail: "เติมแล้ว 4 ช่อง" },
          { name: "template.recalculated", ok: true, label: "คำนวณสูตรในแบบฟอร์มใหม่" },
        ],
      },
    },
    { id: "a3", name: "รายงาน_2569-09.pdf", format: "pdf", size: 8192, version: 1 },
  ],
};

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("OutputsCard with a filled template", () => {
  it("labels the template and the PDF, not the office files", () => {
    render(<OutputsCard outputs={outputs} />);
    const rows = screen.getAllByTestId("am-artifact");
    const byFormat = (f: string) => rows.find((r) => r.getAttribute("data-format") === f)!;
    expect(byFormat("template").textContent).toContain("แบบฟอร์มของคุณ · ");
    expect(byFormat("pdf").textContent).toContain("รายงาน PDF · ");
    expect(byFormat("xlsx").textContent).not.toContain("แบบฟอร์มของคุณ");
    expect(byFormat("xlsx").textContent).not.toContain("Excel (.xlsx)");
    // the icon reads the template's own extension, not ".template"
    expect(byFormat("template").textContent).toContain("XLS");
    expect(byFormat("template").textContent).not.toContain("TEMP");
  });

  it("its checks count and list with the others", () => {
    render(<OutputsCard outputs={outputs} />);
    // AM4: folded for a member — trust signals, not daily reading
    expect(screen.queryByText("ตรวจคุณภาพผ่าน 3 จาก 3")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "รายละเอียดการตรวจ เครดิต และโมเดล" }));
    expect(screen.getByText("ตรวจคุณภาพผ่าน 3 จาก 3")).toBeTruthy();
    expect(screen.getByText("เติมค่าลงแบบฟอร์มได้")).toBeTruthy();
    expect(screen.getByText("คำนวณสูตรในแบบฟอร์มใหม่")).toBeTruthy();
  });
});
