// FIX132 N10 — the model picker's Auto row read "อัตโนมัติ — เลือก model ให้
// อัตโนมัติ": an English word inside a Thai sentence (NIGHT3 j2-03).
import { describe, expect, it } from "vitest";

import { dictionaries } from "./dict";

describe("FIX132 N10 — Thai copy says โมเดล, not model", () => {
  it("the Auto row's description", () => {
    expect(dictionaries.th["model.auto.description"]).toBe("เลือกโมเดลให้อัตโนมัติ");
  });
  it("no Thai string uses the bare English word 'model' in running text", () => {
    const offenders = Object.entries(dictionaries.th).filter(([, v]) => /[\u0E00-\u0E7F][^"]*\bmodel\b/i.test(v) && !/\{model\}/.test(v));
    expect(offenders).toEqual([]);
  });
});
