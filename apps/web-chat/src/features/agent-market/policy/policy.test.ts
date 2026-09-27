// AM2 phase F — the expense policy table and holiday calendar: read from the
// manifest's sample, checked with the server's rules, saved normalised.
import { describe, expect, it } from "vitest";

import { installSteps, themeAsksCompany } from "../install/wizard";
import type { Manifest } from "../types";
import {
  checkHolidays,
  checkPolicy,
  draftFromPolicy,
  edited,
  holidayRows,
  holidaysConfirmed,
  holidaysSetting,
  isIsoDay,
  moneyText,
  plainAmount,
  plainRate,
  policyFromDraft,
} from "./policy";
import { SAMPLE } from "./testData";

describe("the wizard for the expense audit", () => {
  it("asks the policy table first, then the theme (with the company name) and the file set", () => {
    const m: Manifest = { install: { questions: ["company", "policy", "theme", "formats"] } };
    expect(installSteps(m)).toEqual(["policy", "theme", "formats", "selftest"]);
    expect(themeAsksCompany(m)).toBe(true);
  });
});

describe("amounts and rates", () => {
  it("a plain amount: commas, Thai digits and บาท are fine; “ประมาณ 2,000” is not", () => {
    expect(plainAmount("1,500")).toBe("1500");
    expect(plainAmount("๑,๕๐๐.๕")).toBe("1500.5");
    expect(plainAmount("1500 บาท")).toBe("1500");
    expect(plainAmount("฿2,000.00")).toBe("2000.00");
    expect(plainAmount("ประมาณ 2,000")).toBeNull();
    expect(plainAmount("1 500")).toBeNull();
    expect(plainAmount("-5")).toBeNull();
    expect(plainAmount("")).toBeNull();
    expect(moneyText("1500")).toBe("1500.00");
    expect(moneyText("1500.5")).toBe("1500.50");
    expect(moneyText("1500.125")).toBe("1500.125");
  });

  it("a WHT rate is a percent in (0, 100]", () => {
    expect(plainRate("3")).toBe("3");
    expect(plainRate("3%")).toBe("3");
    expect(plainRate("๑.๕")).toBe("1.5");
    expect(plainRate("100")).toBe("100");
    expect(plainRate("0")).toBeNull();
    expect(plainRate("101")).toBeNull();
    expect(plainRate("สามเปอร์เซ็นต์")).toBeNull();
  });
});

describe("draftFromPolicy / policyFromDraft", () => {
  it("reads the manifest's sample as it is", () => {
    const d = draftFromPolicy(SAMPLE);
    expect(d.source).toBe("package_sample");
    expect(d.categories.map((c) => c.code)).toEqual(["meals", "entertainment", "contractor"]);
    expect(d.categories[1]!.needs_purpose).toBe(true);
    expect(d.categories[2]!.wht_rate).toBe("3");
    expect(d.prohibited).toBe("คาราโอเกะ\nผับ");
    expect(checkPolicy(d)).toEqual({});
  });

  it("saves what was typed as plain amounts, and any edit makes it the company's own table", () => {
    const d0 = draftFromPolicy(SAMPLE);
    const d = edited(d0, {
      receipt_required_above: "๑,๐๐๐",
      categories: d0.categories.map((c) => (c.code === "meals" ? { ...c, per_claim: "1,800", per_person_month: "" } : c)),
      prohibited: "คาราโอเกะ\n\n ร้านทอง , บาร์",
      weekend_holiday: "exception",
    });
    expect(d.source).toBe("install");
    const p = policyFromDraft(d);
    expect(p).toMatchObject({
      schema: "am2.expense_policy.v1",
      source: "install",
      currency: "THB",
      receipt_required_above: "1000.00",
      approval_threshold: "3000.00",
      prohibited_merchants: ["คาราโอเกะ", "ร้านทอง", "บาร์"],
      weekend_holiday: "exception",
      vat_claim_requires: "full_tax_invoice",
    });
    expect(p.categories!.meals).toEqual({ label: "ค่าอาหาร", per_claim: "1800.00" });
    expect(p.categories!.entertainment).toEqual({ label: "ค่ารับรองลูกค้า", per_claim: "5000.00", needs_purpose: true });
    expect(p.categories!.contractor).toEqual({ label: "ค่าจ้างบุคคลภายนอก", per_claim: "20000.00", wht_rate: "3", wht_from: "1000.00" });
  });

  it("keeps keys the editor does not show", () => {
    const p = policyFromDraft(draftFromPolicy({ ...SAMPLE, notes: "จากระเบียบปี 2569", categories: { meals: { label: "ค่าอาหาร", gl: "5310" } } }));
    expect(p.notes).toBe("จากระเบียบปี 2569");
    expect(p.categories!.meals).toEqual({ gl: "5310", label: "ค่าอาหาร" });
  });
});

describe("checkPolicy", () => {
  const base = draftFromPolicy(SAMPLE);
  const cat = (patch: Record<string, unknown>) => ({ ...base, categories: [{ ...base.categories[0]!, ...patch }] });

  it("both thresholds are required and must be amounts", () => {
    expect(checkPolicy({ ...base, receipt_required_above: "", approval_threshold: "ประมาณ 3,000" })).toEqual({
      receipt_required_above: "policy.err.required",
      approval_threshold: "policy.err.money",
    });
  });

  it("at least one category, each with a plain unique code", () => {
    expect(checkPolicy({ ...base, categories: [] })).toEqual({ categories: "policy.err.noCategory" });
    const id = base.categories[0]!.id;
    expect(checkPolicy(cat({ code: "ค่าอาหาร" }))[`cat.${id}.code`]).toBe("policy.err.code");
    expect(checkPolicy(cat({ code: "" }))[`cat.${id}.code`]).toBe("policy.err.required");
    const twice = { ...base, categories: [base.categories[0]!, { ...base.categories[1]!, code: "meals" }] };
    expect(checkPolicy(twice)[`cat.${base.categories[1]!.id}.code`]).toBe("policy.err.codeTwice");
  });

  it("a limit that is not an amount, a bad rate, a rate without its starting amount", () => {
    const id = base.categories[0]!.id;
    expect(checkPolicy(cat({ per_claim: "ประมาณ 2,000" }))[`cat.${id}.per_claim`]).toBe("policy.err.money");
    expect(checkPolicy(cat({ wht_rate: "0", wht_from: "1000" }))[`cat.${id}.wht_rate`]).toBe("policy.err.rate");
    expect(checkPolicy(cat({ wht_rate: "3", wht_from: "" }))[`cat.${id}.wht_from`]).toBe("policy.err.whtFrom");
    expect(checkPolicy(cat({ wht_rate: "3", wht_from: "1,000" }))).toEqual({});
  });
});

describe("holidays", () => {
  it("reads {date: name} (or {dates: …}) by date", () => {
    expect(holidayRows({ "2026-12-31": "วันสิ้นปี", "2026-10-23": "วันปิยมหาราช" }).map((r) => [r.date, r.name])).toEqual([
      ["2026-10-23", "วันปิยมหาราช"],
      ["2026-12-31", "วันสิ้นปี"],
    ]);
    expect(holidayRows({ dates: { "2026-05-01": "วันแรงงาน" } }).map((r) => r.date)).toEqual(["2026-05-01"]);
    expect(holidayRows(null)).toEqual([]);
  });

  it("a real date, not twice, with a name; saved only when the company confirmed it (AM3: fail closed)", () => {
    expect(isIsoDay("2026-02-29")).toBe(false);
    expect(isIsoDay("2028-02-29")).toBe(true);
    const rows = [
      { id: "a", date: "2026-10-23", name: "วันปิยมหาราช" },
      { id: "b", date: "2026-10-23", name: "ซ้ำ" },
      { id: "c", date: "2026-13-01", name: "" },
    ];
    expect(checkHolidays(rows)).toEqual({ "b.date": "policy.err.dateTwice", "c.date": "policy.err.date", "c.name": "policy.err.holidayName" });
    // unconfirmed: nothing is saved, so the holiday rule stays off (weekends are still checked)
    expect(holidaysSetting([{ id: "a", date: "2026-10-23", name: " วันปิยมหาราช " }])).toBeNull();
    expect(holidaysSetting([], true, "a@b.test")).toBeNull();
    expect(holidaysSetting([{ id: "a", date: "2026-10-23", name: " วันปิยมหาราช " }], true, "a@b.test", "2026-09-28T03:00:00Z")).toEqual({
      schema: "am3.holidays.v1",
      kind: "company",
      confirmed: true,
      confirmed_by: "a@b.test",
      confirmed_at: "2026-09-28T03:00:00Z",
      dates: { "2026-10-23": "วันปิยมหาราช" },
    });
  });

  it("only a confirmed company calendar turns the check on (an AM2 bare map does not)", () => {
    expect(holidaysConfirmed({ "2026-10-23": "วันปิยมหาราช" })).toBe(false);
    expect(holidaysConfirmed({ kind: "company", confirmed: false, dates: {} })).toBe(false);
    expect(holidaysConfirmed({ kind: "government", confirmed: true, dates: {} })).toBe(false);
    expect(holidaysConfirmed({ kind: "company", confirmed: true, dates: { "2026-10-23": "x" } })).toBe(true);
    expect(holidayRows({ schema: "am3.holidays.v1", kind: "company", confirmed: true, dates: { "2026-10-23": "x" } }).map((r) => r.date)).toEqual([
      "2026-10-23",
    ]);
  });
});
