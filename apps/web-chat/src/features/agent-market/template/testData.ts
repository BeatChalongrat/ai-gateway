// Test data for the template screens (not used by the app): the shape of
// manifest.templates (invoice_processing/agent.json, trimmed), a template's
// slots of every kind and an inspect job's proposal.

import type { TemplateSlot, TemplateSpec } from "../types";

export const spec: TemplateSpec = {
  fields: {
    company: { label: "ชื่อบริษัท", aliases: ["company_name"] },
    period: { label: "งวด" },
    subtotal_thb: { label: "ยอดก่อน VAT (บาท)" },
    total_thb: "ยอดรวม (บาท)",
  },
  lists: {
    lines: { label: "รายการเอกสาร", columns: { date: "วันที่", number: "เลขที่", seller: "ผู้ขาย", total: "ยอดรวม" } },
    other: { label: "รายการอื่น", columns: { a: "คอลัมน์เอ" } },
  },
};

export const slots: TemplateSlot[] = [
  { key: "company", kind: "marker", label: "company", fillable: true, reason: null },
  { key: "line", kind: "list", label: "line", fillable: true, reason: null, columns: ["date", "number", "amount"] },
  { key: "CompanyName", kind: "control", label: "ชื่อบริษัท", fillable: true, reason: null },
  { key: "Total", kind: "name", label: "Total", where: "Sheet1!$B$7", fillable: false, reason: "formula_cell" },
  { key: "Lines", kind: "table", label: "Lines", where: "สรุป!A10:C13", fillable: true, reason: null, columns: ["A", "B", "C"] },
  { key: "Sheet1!B1", kind: "target", label: "บริษัท", where: "Sheet1!B1", fillable: true, reason: null },
  { key: "Locked", kind: "name", label: "Locked", where: "Sheet2!$B$2", fillable: false, reason: "protected_sheet" },
  { key: "DateCtl", kind: "control", label: "วันที่เอกสาร", fillable: false, reason: "not_plain_text" },
];

export const proposal: Record<string, string> = {
  company: "company",
  line: "lines",
  "line.date": "lines.date",
  "line.number": "lines.number",
  Total: "total_thb", // a formula cell: never offered, never sent
  "Sheet1!B1": "no_such_field",
  Lines: "lines",
  "Lines.A": "other.a", // a column from another list
};
