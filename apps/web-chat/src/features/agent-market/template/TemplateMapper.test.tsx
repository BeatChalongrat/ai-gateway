// AM2 Phase E — the template mapping screen and its settings section.
//   * one row per slot: a fillable one has a select (proposal preselected),
//     one that cannot be filled is disabled with the reason in plain Thai;
//   * the selects build the mapping — a single place, a list and its
//     columns — and PUT sends only the mapped entries; BAD_MAPPING is shown;
//   * a new version: the summary line, "ใหม่" on added slots, the removed
//     ones listed by what they were;
//   * the section: the saved template, upload → inspect job → the screen,
//     and "stop using a template" (DELETE) after an inline confirm.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw, uploadTemplate } = vi.hoisted(() => ({ raw: vi.fn(), uploadTemplate: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("../api", async (importOriginal) => {
  const m = await importOriginal<typeof import("../api")>();
  return { ...m, amApi: { ...m.amApi, uploadTemplate } };
});

import { useI18nStore } from "@/lib/i18n";

import { AmError } from "../api";
import type { SavedTemplate, TemplateInspect } from "../types";
import { TemplateMapper } from "./TemplateMapper";
import { TemplateSection } from "./TemplateSection";
import { proposal, slots, spec } from "./testData";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
const first: TemplateInspect = { slots, proposal, diff: { first: true, kept: [], added: slots.map((s) => s.key), removed: [] } };

beforeEach(() => {
  raw.mockReset();
  uploadTemplate.mockReset();
  raw.mockResolvedValue(json({ template: {} }));
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

function row(key: string): HTMLElement {
  const el = screen.getAllByTestId("am-template-slot").find((r) => r.getAttribute("data-key") === key);
  expect(el, `no row for ${key}`).toBeTruthy();
  return el!;
}
const select = (label: string) => screen.getByRole("combobox", { name: label }) as HTMLSelectElement;
const putBody = () => {
  const call = raw.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PUT");
  expect(call, "no PUT").toBeTruthy();
  expect(call![0]).toBe("/agent-market/installs/i-1/template");
  return JSON.parse(String((call![1] as RequestInit).body));
};

function mapper(inspect: TemplateInspect = first, saved: SavedTemplate | null = null, onSaved = vi.fn()) {
  render(<TemplateMapper installId="i-1" spec={spec} fileId="f-9" fileName="แบบฟอร์มซื้อ.xlsx" inspect={inspect} saved={saved} onSaved={onSaved} onCancel={vi.fn()} />);
  return onSaved;
}

describe("TemplateMapper: slots", () => {
  it("a fillable slot gets a select with the proposal chosen; one that cannot be filled is disabled with its reason", () => {
    mapper();
    expect(screen.getAllByTestId("am-template-slot")).toHaveLength(slots.length);
    expect(select("ค่าสำหรับ {{company}}").value).toBe("company");
    expect(select("ค่าสำหรับ {{line.…}}").value).toBe("lines");
    expect(select("{{line.date}} ของ {{line.…}}").value).toBe("lines.date");
    expect(select("ค่าสำหรับ บริษัท (Sheet1!B1)").value).toBe(""); // proposed a field the agent does not have
    expect(select("คอลัมน์ A ของ Lines (สรุป!A10:C13)").value).toBe(""); // proposed from another list
    // the options: "not used" first, then the agent's values
    expect([...select("ค่าสำหรับ {{company}}").options].map((o) => o.textContent)).toEqual(["— ไม่ใช้ —", "ชื่อบริษัท", "งวด", "ยอดก่อน VAT (บาท)", "ยอดรวม (บาท)"]);
    expect([...select("ค่าสำหรับ Lines (สรุป!A10:C13)").options].map((o) => o.textContent)).toEqual(["— ไม่ใช้ —", "รายการเอกสาร", "รายการอื่น"]);
    expect([...select("คอลัมน์ B ของ Lines (สรุป!A10:C13)").options].map((o) => o.textContent)).toEqual(["— ไม่ใช้ —", "วันที่", "เลขที่", "ผู้ขาย", "ยอดรวม"]);

    const reasons: Record<string, string> = {
      Total: "ช่องสูตร คำนวณในไฟล์เอง",
      Locked: "ชีตถูกล็อก",
      DateCtl: "ช่องชนิดนี้เติมไม่ได้ (เช่น รายการตัวเลือก/วันที่)",
    };
    for (const [key, text] of Object.entries(reasons)) {
      const r = row(key);
      expect(r.getAttribute("data-fillable")).toBe("false");
      expect(within(r).getByTestId("am-template-reason").textContent).toBe(text);
      const s = within(r).getByTestId("am-template-select") as HTMLSelectElement;
      expect(s.disabled).toBe(true);
      expect(s.options).toHaveLength(1);
      expect(s.options[0]!.textContent).toBe("เติมไม่ได้");
    }
    expect(within(row("company")).queryByTestId("am-template-reason")).toBeNull();
    expect(screen.getByTestId("am-template-count").textContent).toBe("เลือกแล้ว 3 จาก 5 ช่อง");
  });

  it("an unknown reason code still gets a sentence", () => {
    mapper({ ...first, slots: [{ key: "X", kind: "name", label: "X", where: "A!B1", fillable: false, reason: "brand_new_code" }] });
    expect(screen.getByTestId("am-template-reason").textContent).toBe("เติมค่าในช่องนี้ไม่ได้ (brand_new_code)");
  });

  it("a file with nothing to fill says how to make one, and offers no save", () => {
    mapper({ ...first, slots: [], proposal: {} });
    expect(screen.getByTestId("am-template-empty").textContent).toContain("{{ชื่อช่อง}}");
    expect(screen.queryByTestId("am-template-save")).toBeNull();
  });
});

describe("TemplateMapper: the mapping and the PUT body", () => {
  it("builds the mapping from the selects and sends only mapped entries", async () => {
    const onSaved = mapper();
    fireEvent.change(select("ค่าสำหรับ ชื่อบริษัท"), { target: { value: "company" } });
    fireEvent.change(select("ค่าสำหรับ บริษัท (Sheet1!B1)"), { target: { value: "period" } });
    fireEvent.change(select("{{line.amount}} ของ {{line.…}}"), { target: { value: "lines.total" } });
    fireEvent.change(select("คอลัมน์ B ของ Lines (สรุป!A10:C13)"), { target: { value: "lines.total" } });
    // "not used" takes a place back out
    fireEvent.change(select("{{line.number}} ของ {{line.…}}"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("am-template-save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(putBody()).toEqual({
      file_id: "f-9",
      slots,
      mapping: {
        company: "company",
        line: "lines",
        "line.date": "lines.date",
        "line.amount": "lines.total",
        CompanyName: "company",
        Lines: "lines",
        "Lines.B": "lines.total",
        "Sheet1!B1": "period",
      },
    });
  });

  it("another list for a list slot starts its columns over", () => {
    mapper();
    fireEvent.change(select("ค่าสำหรับ {{line.…}}"), { target: { value: "other" } });
    expect(select("{{line.date}} ของ {{line.…}}").value).toBe("");
    expect([...select("{{line.date}} ของ {{line.…}}").options].map((o) => o.value)).toEqual(["", "other.a"]);
    fireEvent.change(select("ค่าสำหรับ {{line.…}}"), { target: { value: "" } });
    expect(select("{{line.date}} ของ {{line.…}}").disabled).toBe(true);
  });

  it("nothing mapped: save waits", () => {
    mapper({ ...first, proposal: {} });
    expect((screen.getByTestId("am-template-save") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("am-template-count").textContent).toBe("เลือกแล้ว 0 จาก 5 ช่อง · เลือกค่าให้อย่างน้อย 1 ช่อง");
  });

  it("the server's refusal (BAD_MAPPING) is shown and the screen stays", async () => {
    raw.mockResolvedValue(json({ error: { code: "BAD_MAPPING", message: "คอลัมน์ “B” ต้องมาจากรายการเดียวกับช่อง “Lines”" } }, 400));
    const onSaved = mapper();
    fireEvent.click(screen.getByTestId("am-template-save"));
    expect(await screen.findByText("คอลัมน์ “B” ต้องมาจากรายการเดียวกับช่อง “Lines”")).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
    expect((screen.getByTestId("am-template-save") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("TemplateMapper: a new version", () => {
  const saved: SavedTemplate = {
    file_id: "f-1",
    name: "แบบฟอร์มซื้อ.xlsx",
    kind: "xlsx",
    version: 1,
    slots: [
      { key: "company", kind: "marker", label: "company", fillable: true, reason: null },
      { key: "OldTotal", kind: "name", label: "OldTotal", where: "Sheet1!$B$9", fillable: true, reason: null },
      { key: "vat", kind: "marker", label: "vat", fillable: true, reason: null },
    ],
    mapping: { company: "company", OldTotal: "total_thb", vat: "total_thb" },
    saved_at: "2026-09-01T10:00:00Z",
  };
  const next: TemplateInspect = {
    slots,
    proposal,
    diff: { first: false, kept: ["company"], added: ["CompanyName", "Lines", "Sheet1!B1"], removed: ["OldTotal", "vat"] },
  };

  it("summary line, 'ใหม่' on added slots, removed ones listed by what they were", () => {
    mapper(next, saved);
    expect(screen.getByTestId("am-template-diff").textContent).toBe("รุ่นใหม่: เหมือนเดิม 1 ช่อง · เพิ่ม 3 · หาย 2");
    for (const key of ["CompanyName", "Lines", "Sheet1!B1"]) expect(within(row(key)).getByTestId("am-template-new").textContent).toBe("ใหม่");
    expect(within(row("company")).queryByTestId("am-template-new")).toBeNull();
    const removed = screen.getByTestId("am-template-removed");
    expect(removed.textContent).toContain("ช่องที่หายไปจากรุ่นนี้");
    expect([...removed.querySelectorAll("li")].map((li) => li.textContent)).toEqual(["OldTotal (Sheet1!$B$9)", "{{vat}}"]);
  });

  it("a first template has no summary and no 'ใหม่' badges", () => {
    mapper();
    expect(screen.queryByTestId("am-template-diff")).toBeNull();
    expect(screen.queryByTestId("am-template-new")).toBeNull();
    expect(screen.queryByTestId("am-template-removed")).toBeNull();
  });
});

describe("TemplateSection", () => {
  const saved: SavedTemplate = {
    file_id: "f-1",
    name: "แบบฟอร์มซื้อ.xlsx",
    kind: "xlsx",
    version: 3,
    slots: slots,
    mapping: { company: "company", line: "lines", "line.date": "lines.date" },
    saved_at: "2026-09-20T10:00:00Z",
  };

  it("none saved: what a template does, and an upload button", () => {
    render(<TemplateSection installId="i-1" spec={spec} saved={null} onChanged={vi.fn()} />);
    expect(screen.getByTestId("am-template-explain").textContent).toBe(
      "ทุกรอบ agent จะเติมตัวเลขลงแบบฟอร์ม .docx .xlsx หรือ .pptx ของคุณ เขียนเฉพาะช่องกรอก และคำนวณสูตรในไฟล์ใหม่ให้",
    );
    expect(screen.getByTestId("am-template-upload").textContent).toBe("อัปโหลดแบบฟอร์ม");
    expect(screen.queryByTestId("am-template-stop")).toBeNull();
    expect(screen.getByTestId("am-template-file").getAttribute("accept")).toBe(".docx,.xlsx,.xlsm,.pptx");
  });

  it("saved: name, version and date, mapped count; stop using it after a confirm", async () => {
    raw.mockResolvedValue(json({ removed: true }));
    const onChanged = vi.fn();
    render(<TemplateSection installId="i-1" spec={spec} saved={saved} onChanged={onChanged} />);
    const box = screen.getByTestId("am-template-saved");
    expect(box.textContent).toContain("แบบฟอร์มซื้อ.xlsx");
    expect(box.textContent).toContain("รุ่นที่ 3");
    expect(box.textContent).toContain("จับคู่ไว้ 2 ช่อง");
    expect(screen.getByTestId("am-template-upload").textContent).toBe("เปลี่ยนเป็นรุ่นใหม่");
    fireEvent.click(screen.getByTestId("am-template-stop"));
    expect(raw).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("am-template-stop-yes"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(raw.mock.calls[0]![0]).toBe("/agent-market/installs/i-1/template");
    expect((raw.mock.calls[0]![1] as RequestInit).method).toBe("DELETE");
  });

  it("upload → the inspect job → the mapping screen, and a save goes back to the saved view", async () => {
    uploadTemplate.mockResolvedValue({ file: { id: "f-9", name: "แบบฟอร์มซื้อ.xlsx", size: 10 }, kind: "xlsx", job: { id: "job-1" } });
    raw.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/agent-market/runs/job-1") {
        return json({ run: { id: "job-1", status: "succeeded" }, steps: [{ id: "s1", seq: 1, name: "template:แบบฟอร์มซื้อ.xlsx", kind: "ingest", status: "done", output: first }] });
      }
      if (init?.method === "PUT") return json({ template: {} });
      return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
    });
    const onChanged = vi.fn();
    render(<TemplateSection installId="i-1" spec={spec} saved={null} onChanged={onChanged} pollMs={1} />);
    fireEvent.change(screen.getByTestId("am-template-file"), { target: { files: [new File(["x"], "แบบฟอร์มซื้อ.xlsx")] } });
    await screen.findByTestId("am-template-mapper");
    expect(uploadTemplate).toHaveBeenCalledWith("i-1", expect.any(File), expect.any(Object));
    expect(screen.getAllByTestId("am-template-slot")).toHaveLength(slots.length);
    fireEvent.click(screen.getByTestId("am-template-save"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(putBody().file_id).toBe("f-9");
    expect(screen.queryByTestId("am-template-mapper")).toBeNull();
  });

  it("an agent without templates (409) or a wrong file says why", async () => {
    uploadTemplate.mockRejectedValue(new AmError("NO_TEMPLATES", "agent ตัวนี้ยังไม่รองรับแบบฟอร์มของคุณ", 409));
    render(<TemplateSection installId="i-1" spec={spec} saved={null} onChanged={vi.fn()} pollMs={1} />);
    fireEvent.change(screen.getByTestId("am-template-file"), { target: { files: [new File(["x"], "ฟอร์ม.docx")] } });
    expect(await screen.findByText("agent ตัวนี้ยังไม่รองรับแบบฟอร์มของคุณ")).toBeTruthy();
    fireEvent.change(screen.getByTestId("am-template-file"), { target: { files: [new File(["x"], "ฟอร์ม.pdf")] } });
    expect(await screen.findByText("รับเฉพาะไฟล์ .docx .xlsx .xlsm .pptx")).toBeTruthy();
    expect(uploadTemplate).toHaveBeenCalledTimes(1);
  });
});
