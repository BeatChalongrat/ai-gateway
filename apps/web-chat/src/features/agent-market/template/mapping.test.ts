// AM2 Phase E — the template mapping screen's rules: what it starts from,
// what a list choice does to its columns, what PUT sends (only mapped
// entries the server accepts), the new-version counts and the words.
import { describe, expect, it } from "vitest";

import type { Default } from "../types";
import {
  buildMapping,
  chooseList,
  columnOptions,
  columnTitle,
  diffCounts,
  fieldOptions,
  initialSelection,
  listOptions,
  mappedCount,
  reasonKey,
  saveBody,
  savedTemplateOf,
  slotTitle,
  takesTemplates,
} from "./mapping";
import { proposal, slots, spec } from "./testData";

describe("the agent's values", () => {
  it("fields, lists and a list's columns in manifest order", () => {
    expect(fieldOptions(spec)).toEqual([
      { key: "company", label: "ชื่อบริษัท" },
      { key: "period", label: "งวด" },
      { key: "subtotal_thb", label: "ยอดก่อน VAT (บาท)" },
      { key: "total_thb", label: "ยอดรวม (บาท)" },
    ]);
    expect(listOptions(spec).map((o) => o.key)).toEqual(["lines", "other"]);
    expect(columnOptions(spec, "lines").map((o) => o.key)).toEqual(["lines.date", "lines.number", "lines.seller", "lines.total"]);
    expect(takesTemplates(spec)).toBe(true);
    expect(takesTemplates({})).toBe(false);
    expect(takesTemplates(null)).toBe(false);
  });
});

describe("initialSelection", () => {
  it("keeps the proposal the server would accept, and nothing for a place that cannot be filled", () => {
    expect(initialSelection(slots, proposal, spec)).toEqual({
      company: "company",
      line: "lines",
      "line.date": "lines.date",
      "line.number": "lines.number",
      "line.amount": "",
      CompanyName: "",
      Lines: "lines",
      "Lines.A": "",
      "Lines.B": "",
      "Lines.C": "",
      "Sheet1!B1": "",
    });
  });
});

describe("chooseList", () => {
  it("a list's columns start over from the names that match", () => {
    const line = slots[1]!;
    const sel = chooseList({ line: "", "line.date": "", "line.number": "", "line.amount": "" }, line, "lines", spec);
    expect(sel).toEqual({ line: "lines", "line.date": "lines.date", "line.number": "lines.number", "line.amount": "" });
    expect(chooseList(sel, line, "", spec)).toEqual({ line: "", "line.date": "", "line.number": "", "line.amount": "" });
  });
});

describe("buildMapping / saveBody", () => {
  it("sends only mapped entries: a single place, a list and its columns", () => {
    const sel = {
      ...initialSelection(slots, proposal, spec),
      CompanyName: "company",
      "line.amount": "lines.total",
      "Lines.B": "lines.total",
      "Sheet1!B1": "period",
    };
    expect(buildMapping(slots, sel, spec)).toEqual({
      company: "company",
      line: "lines",
      "line.date": "lines.date",
      "line.number": "lines.number",
      "line.amount": "lines.total",
      CompanyName: "company",
      Lines: "lines",
      "Lines.B": "lines.total",
      "Sheet1!B1": "period",
    });
  });

  it("drops a column whose list is not its slot's, a slot left unused, and a place that cannot be filled", () => {
    const sel = { line: "", "line.date": "lines.date", Lines: "lines", "Lines.A": "other.a", Total: "total_thb", company: "no_such_field" };
    expect(buildMapping(slots, sel, spec)).toEqual({ Lines: "lines" });
  });

  it("the PUT body carries the file, every slot and the mapping", () => {
    const body = saveBody("f-9", slots, { company: "company" }, spec);
    expect(body).toEqual({ file_id: "f-9", slots, mapping: { company: "company" } });
  });

  it("counts a list once", () => {
    expect(mappedCount(slots, { company: "company", line: "lines", "line.date": "lines.date", Total: "total_thb" })).toBe(2);
  });
});

describe("a new version", () => {
  it("kept / added / removed counts; none for a first template", () => {
    expect(diffCounts({ first: false, kept: ["a", "b", "c"], added: ["d"], removed: ["e", "f"] })).toEqual({ kept: 3, added: 1, removed: 2 });
    expect(diffCounts({ first: true, kept: [], added: ["a"], removed: [] })).toBeNull();
    expect(diffCounts(null)).toBeNull();
  });
});

describe("words", () => {
  it("each slot as it appears in the file", () => {
    expect(slots.map(slotTitle)).toEqual([
      "{{company}}",
      "{{line.…}}",
      "ชื่อบริษัท",
      "Total (Sheet1!$B$7)",
      "Lines (สรุป!A10:C13)",
      "บริษัท (Sheet1!B1)",
      "Locked (Sheet2!$B$2)",
      "วันที่เอกสาร",
    ]);
    expect(columnTitle(slots[1]!, "date")).toBe("{{line.date}}");
    expect(columnTitle(slots[4]!, "A")).toBeNull();
  });

  it("every reason has a sentence, an unknown one too", () => {
    expect(reasonKey("formula_cell")).toBe("tpl.reason.formula_cell");
    expect(reasonKey("protected_sheet")).toBe("tpl.reason.protected_sheet");
    expect(reasonKey("not_plain_text")).toBe("tpl.reason.not_plain_text");
    expect(reasonKey("something_new")).toBe("tpl.reason.other");
    expect(reasonKey(null)).toBe("tpl.reason.none");
  });

  it("reads the saved template from the install defaults", () => {
    const d = (key: string, value: unknown): Default => ({ key, value, source: "settings", label: "", note: "", updated_at: "" });
    const saved = { file_id: "f-1", name: "ฟอร์ม.xlsx", kind: "xlsx", version: 2, slots: [], mapping: {}, saved_at: "2026-09-26T10:00:00Z" };
    expect(savedTemplateOf([d("formats", ["xlsx"]), d("template", saved)])).toEqual(saved);
    expect(savedTemplateOf([d("formats", ["xlsx"])])).toBeNull();
    expect(savedTemplateOf([d("template", { name: "x" })])).toBeNull();
  });
});
