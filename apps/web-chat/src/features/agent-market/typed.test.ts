// AM2 — values a person types: the company's tax ID (mod-11) and branch, and
// a card's typed answer per input kind (the rules of engine/e2/typed.py).
import { describe, expect, it } from "vitest";

import { arabicDigits, checkBranch, checkTaxId, checkTyped, formatTaxId, normalizeTaxId, questionInput, taxIdCheckOk } from "./typed";

describe("tax ID", () => {
  it("accepts IDs whose check digit holds", () => {
    expect(taxIdCheckOk("0105545009875")).toBe(true);
    expect(taxIdCheckOk("0105563012343")).toBe(true);
    expect(checkTaxId("0105545009875")).toEqual({ ok: true, value: "0105545009875" });
    expect(checkTaxId("0105563012343")).toEqual({ ok: true, value: "0105563012343" });
  });

  it("refuses a wrong check digit, and says why", () => {
    expect(taxIdCheckOk("0105545009870")).toBe(false);
    expect(checkTaxId("0105545009870")).toEqual({ ok: false, reason: "typed.err.taxIdCheck" });
  });

  it("normalises Thai digits, spaces and hyphens", () => {
    expect(arabicDigits("๐๑๐๕๕๔๕๐๐๙๘๗๕")).toBe("0105545009875");
    expect(normalizeTaxId("๐๑๐๕๕๔๕๐๐๙๘๗๕")).toBe("0105545009875");
    expect(checkTaxId("๐๑๐๕๕๔๕๐๐๙๘๗๕")).toEqual({ ok: true, value: "0105545009875" });
    expect(checkTaxId("0-1055-45009-87-5")).toEqual({ ok: true, value: "0105545009875" });
    expect(checkTaxId(" 0105 54500 9875 ")).toEqual({ ok: true, value: "0105545009875" });
  });

  it("needs exactly 13 digits", () => {
    expect(checkTaxId("")).toEqual({ ok: false, reason: "typed.err.empty" });
    expect(checkTaxId("010554500987")).toEqual({ ok: false, reason: "typed.err.taxIdLength" });
    expect(checkTaxId("01055450098751")).toEqual({ ok: false, reason: "typed.err.taxIdLength" });
    expect(checkTaxId("0105545O09875")).toEqual({ ok: false, reason: "typed.err.taxIdLength" });
  });

  it("formats 13 digits the way Thai documents print them", () => {
    expect(formatTaxId("0105545009875")).toBe("0-1055-45009-87-5");
    expect(formatTaxId("123")).toBe("123");
  });
});

describe("branch", () => {
  it("is 5 digits and never 00000 (the head office)", () => {
    expect(checkBranch("00001")).toEqual({ ok: true, value: "00001" });
    expect(checkBranch("๐๐๐๑๒")).toEqual({ ok: true, value: "00012" });
    expect(checkBranch("12")).toEqual({ ok: false, reason: "companyTax.err.branch" });
    expect(checkBranch("00000")).toEqual({ ok: false, reason: "companyTax.err.branchHead" });
  });
});

describe("checkTyped", () => {
  const ok = (kind: string, text: string) => checkTyped(kind, text).ok;

  it("money: thousands commas, at most 2 decimals, Thai digits and บาท allowed", () => {
    for (const v of ["12,850.70", "12850.7", "12850", "1,234", "๑๒,๘๕๐.๗๐", "12,850.70 บาท", "฿500", "-100.00"]) expect(ok("money", v), v).toBe(true);
    for (const v of ["12,85", "12.345", "12,8500.00", "abc", "1.2.3", "", "   "]) expect(ok("money", v), v).toBe(false);
    expect(checkTyped("money", "12,850.70 บาท")).toEqual({ ok: true, value: "12850.70" });
    expect(checkTyped("money", "12.345")).toEqual({ ok: false, reason: "typed.err.money" });
  });

  it("tax_id: 13 digits with a valid check digit", () => {
    expect(ok("tax_id", "0105545009875")).toBe(true);
    expect(ok("tax_id", "๐๑๐๕๕๔๕๐๐๙๘๗๕")).toBe(true);
    expect(checkTyped("tax_id", "0105545009870")).toEqual({ ok: false, reason: "typed.err.taxIdCheck" });
    expect(checkTyped("tax_id", "12345")).toEqual({ ok: false, reason: "typed.err.taxIdLength" });
  });

  it("date: dd/mm/yyyy in BE or CE, or yyyy-mm-dd; a real calendar day", () => {
    for (const v of ["15/09/2569", "15/09/2026", "1/9/2569", "2026-09-15", "๑๕/๐๙/๒๕๖๙", "29/02/2567"]) expect(ok("date", v), v).toBe(true);
    for (const v of ["31/02/2569", "15/13/2569", "15/09/69", "2026/9", "yesterday", "29/02/2566"]) expect(ok("date", v), v).toBe(false);
    expect(checkTyped("date", "31/02/2569")).toEqual({ ok: false, reason: "typed.err.date" });
  });

  it("rate: a number above 0", () => {
    for (const v of ["36.25", "1", "0.5", "๓๖.๒๕", "1,000.5"]) expect(ok("rate", v), v).toBe(true);
    for (const v of ["0", "0.00", "-1", "abc", ""]) expect(ok("rate", v), v).toBe(false);
  });

  it("text (and an unknown kind): anything but empty", () => {
    expect(ok("text", "บริษัท ตัวอย่าง จำกัด")).toBe(true);
    expect(checkTyped("text", "   ")).toEqual({ ok: false, reason: "typed.err.empty" });
    expect(ok("percent", "12")).toBe(true);
  });
});

describe("questionInput", () => {
  it("reads the wire shape and falls back to text for an unknown kind", () => {
    expect(questionInput({ kind: "money", hint: "พิมพ์ตัวเลข เช่น 12,850.70" })).toEqual({ kind: "money", hint: "พิมพ์ตัวเลข เช่น 12,850.70" });
    expect(questionInput({ kind: "percent" })).toEqual({ kind: "text", hint: "" });
    expect(questionInput(null)).toBeNull();
    expect(questionInput(undefined)).toBeNull();
    expect(questionInput("money")).toBeNull();
  });
});
