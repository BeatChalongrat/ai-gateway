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
    expect(screen.queryByText("เปิดไฟล์ตรวจซ้ำ ผ่านทุกเรื่อง")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "รายละเอียดการตรวจ เครดิต และโมเดล" }));
    expect(screen.getByText("เปิดไฟล์ตรวจซ้ำ ผ่านทุกเรื่อง")).toBeTruthy();
    expect(screen.getByText("เติมค่าลงแบบฟอร์มได้")).toBeTruthy();
    expect(screen.getByText("คำนวณสูตรในแบบฟอร์มใหม่")).toBeTruthy();
  });
});

describe("AM8: a customer form delivered without passing", () => {
  it("says so on its own row and lists every slot it lacks, without opening the details", () => {
    render(
      <OutputsCard
        outputs={{
          run_id: "r-8",
          artifacts: [
            { id: "a", name: "report.docx", format: "docx", size: 10, version: 1, quality: { passed: true, checks: [] } },
            {
              id: "b", name: "form_template_v1.docx", format: "template", size: 10, version: 1,
              quality: {
                passed: false,
                checks: [{ name: "template.every_slot", ok: false, label: "ทุกช่องที่จับคู่ไว้มีค่า" }],
                unfilled: [{ label: "missing_total", why: "รอบนี้ไม่มีค่าให้ช่องนี้" }, { label: "ตาราง 1 แถว 1 ช่อง 1", why: "ข้อความยาวเกินช่องที่สูงคงที่ จะถูกตัด" }],
              },
            },
          ],
        }}
      />,
    );
    const rows = screen.getAllByTestId("am-artifact");
    const tpl = rows.find((r) => r.getAttribute("data-format") === "template")!;
    const note = tpl.querySelector("[data-testid='am-artifact-not-passed']")!;
    expect(note.textContent).toContain("แบบฟอร์มนี้ยังกรอกไม่ครบ 2 ช่อง — อย่าเพิ่งส่งต่อ");
    expect(note.textContent).toContain("missing_total — รอบนี้ไม่มีค่าให้ช่องนี้");
    expect(note.textContent).toContain("ตาราง 1 แถว 1 ช่อง 1");
    const report = rows.find((r) => r.getAttribute("data-format") === "docx")!;
    expect(report.querySelector("[data-testid='am-artifact-not-passed']")).toBeNull();
  });
});
