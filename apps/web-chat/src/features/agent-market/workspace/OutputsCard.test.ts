import { describe, expect, it } from "vitest";

import { amDictionaries } from "../i18n";
import { artifactExtension, artifactLabelKey, FORMAT_KEYS, MORE_FORMATS, moreFormatsOf, qualityOf } from "./OutputsCard";

// AM1b: PDF left the "more files" menu (its Thai shaping was wrong); in AM2 it
// is the finished .docx converted by LibreOffice, made only while a runner has
// the converter — so a delivered PDF has a label, and the menu still offers none.
describe("output formats", () => {
  it("the 'more files' menu offers no PDF on a card from before AM8", () => {
    expect(MORE_FORMATS).not.toContain("pdf");
    expect(moreFormatsOf({})).toEqual(MORE_FORMATS);
  });

  it("AM8: the menu offers what the server says this agent can make — no .docx for an .xlsx-only agent, PDF when there is one", () => {
    expect(moreFormatsOf({ more_formats: ["xlsx"] })).toEqual(["xlsx"]);
    expect(moreFormatsOf({ more_formats: ["docx", "pptx", "xlsx", "pdf"] })).toContain("pdf");
  });

  it("AM8: the quality line counts the figures read back, not the checks", () => {
    const q = qualityOf([{ quality: { checks: [{ name: "docx.figures", ok: true, values: 61 }, { name: "docx.fonts", ok: true }] } },
                         { quality: { checks: [{ name: "xlsx.figures", ok: true, values: 40 }] } }]);
    expect(q.values).toBe(101);
    expect(q.total).toBe(3);
  });

  it("a delivered PDF and a filled template have labels", () => {
    expect(amDictionaries.th[FORMAT_KEYS.pdf!]).toBe("รายงาน PDF");
    expect(amDictionaries.th[FORMAT_KEYS.template!]).toBe("แบบฟอร์มของคุณ");
  });

  it("a row labels only what its file name does not say", () => {
    expect(artifactLabelKey("template")).toBe("outputs.fmt.template");
    expect(artifactLabelKey("pdf")).toBe("outputs.fmt.pdf");
    expect(artifactLabelKey("xlsx")).toBeNull();
    expect(artifactLabelKey("docx")).toBeNull();
  });

  it("a filled template's icon follows its own extension", () => {
    expect(artifactExtension({ format: "template", name: "ทะเบียนซื้อ_2569-09_template_v1.xlsx" })).toBe(".xlsx");
    expect(artifactExtension({ format: "template", name: "report_template_v2.DOCX" })).toBe(".docx");
    expect(artifactExtension({ format: "template", name: "no-extension" })).toBe(".template");
    expect(artifactExtension({ format: "pptx", name: "deck.pptx" })).toBe(".pptx");
  });

  it("a template's quality checks count like any other", () => {
    const q = qualityOf([
      { quality: { checks: [{ name: "xlsx.opens", ok: true }] } },
      {
        quality: {
          checks: [
            { name: "template.filled", ok: true, label: "เติมค่าลงแบบฟอร์มได้" },
            { name: "template.no_silent_gap", ok: true, label: "ไม่มีช่องที่หายไปเงียบ ๆ" },
            { name: "template.formulas_kept", ok: true, label: "ไม่เขียนทับสูตร" },
            { name: "template.parts_kept", ok: true, label: "ส่วนประกอบของไฟล์ครบ" },
            { name: "template.recalculated", ok: false, label: "คำนวณสูตรในแบบฟอร์มใหม่", detail: "สูตรให้ค่าผิดพลาด: B7=#REF!" },
          ],
        },
      },
    ]);
    expect(q.total).toBe(6);
    expect(q.pass).toBe(5);
    expect(q.checks.map((c) => c.name)).toContain("template.recalculated");
  });
});
