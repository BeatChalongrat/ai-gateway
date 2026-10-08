// AM13b — masking follows where the data goes, and a round's card says which of the two it used.
//
// The card's "seen by" lines always said which model saw what. They now also say what was done with what a
// model was sent: not masked (the model runs in the organisation and the agent's data may not leave it), or
// masked first. The word comes from the gateway, by way of the runner ("off" / "on" / "mixed"); a card with no
// such word — no model was called, or a round older than this — says nothing about masking, in either language.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";

import type { OutputsModel } from "../types";
import { SeenBy } from "./OutputsCard";

const card = (model: OutputsModel) => <SeenBy outputs={{ run_id: "r-1", artifacts: [], model }} />;
const box = () => screen.getByTestId("am-outputs-seen-by");
const lines = () => Array.from(box().querySelectorAll("p")).map((p) => p.textContent ?? "");
const rules: OutputsModel = { uses: "text", choice: "auto", written_by_rules: true };

describe("the files card says whether what a model was sent was masked", () => {
  beforeEach(() => useI18nStore.setState({ locale: "th" }));
  afterEach(cleanup);

  it("egress off on an in-house model: not masked, and why", () => {
    render(card({ ...rules, masking: "off" }));
    expect(box().getAttribute("data-masking")).toBe("off");
    expect(lines()).toContain(
      "สิ่งที่ส่งให้โมเดลในรอบนี้ไม่ถูกปิดบัง — โมเดลอยู่ในองค์กร และ agent นี้ปิดการส่งข้อมูลออกนอกองค์กร",
    );
    expect(box().textContent).not.toContain("ถูกปิดบังก่อนส่ง");
  });

  it("egress on, or a model outside: masked first", () => {
    render(card({ uses: "text", choice: "auto", model: "deepseek-v4-flash", self_hosted: false, masking: "on" }));
    expect(box().getAttribute("data-masking")).toBe("on");
    expect(lines()).toContain("ข้อมูลส่วนบุคคลในสิ่งที่ส่งให้โมเดลในรอบนี้ถูกปิดบังก่อนส่ง");
    expect(box().textContent).not.toContain("ไม่ถูกปิดบัง");
  });

  it("some steps of each: says exactly that", () => {
    render(card({ ...rules, masking: "mixed" }));
    expect(lines()).toContain("รอบนี้บางขั้นตอนปิดบังข้อมูลส่วนบุคคลก่อนส่งให้โมเดล บางขั้นตอนส่งให้โมเดลในองค์กรโดยไม่ปิดบัง");
  });

  it("no word from the gateway — no model was called, or an older round: nothing is claimed either way", () => {
    for (const model of [rules, { ...rules, masking: "" }, { ...rules, masking: "yes" }] as OutputsModel[]) {
      const { unmount } = render(card(model));
      expect(box().getAttribute("data-masking")).toBeNull();
      expect(box().textContent).not.toContain("ปิดบัง");
      expect(lines()).toHaveLength(1);
      unmount();
    }
  });

  it("the line is one more line: what the card already said about models is still there", () => {
    render(card({ uses: "text", choice: "auto", model: "qwen3.8-27b-fp8", self_hosted: true, masking: "off" }));
    expect(lines()).toHaveLength(2);
    expect(lines()[0]).toContain("qwen3.8-27b-fp8 (ในองค์กร)");
  });

  it("in English on an English screen", () => {
    useI18nStore.setState({ locale: "en" });
    render(card({ ...rules, masking: "off" }));
    expect(lines()).toContain(
      "What was sent to a model in this run was not masked — the model runs in the organisation and this agent's data may not leave it",
    );
    cleanup();
    render(card({ ...rules, masking: "on" }));
    expect(lines()).toContain("Personal data in what was sent to a model in this run was masked first");
    expect(box().textContent).not.toMatch(/[฀-๿]/);
  });
});
