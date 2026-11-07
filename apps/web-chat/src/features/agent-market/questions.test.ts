// AM1 — card payload ↔ QuestionCard conversion.
import { describe, expect, it } from "vitest";

import { defaultLabels, isWaitOption, toAnswerBody, toPendingQuestion } from "./questions";
import type { CardPayload, Question } from "./types";

const dc: Question = {
  key: "ambiguous_debit_credit",
  code: "ambiguous_debit_credit",
  severity: "blocking",
  header: "เดบิต/เครดิต",
  question: "คอลัมน์ “Dr/Cr” คือเดบิตและเครดิตใช่ไหมครับ",
  multiSelect: false,
  options: [
    { label: "ใช่ D คือเดบิต", description: "รายได้รวมจะเป็น 12,480,300", value: { D: "debit", C: "credit" } },
    { label: "ไม่ใช่ D คือเครดิต", description: "รายได้รวมจะเป็น -12,480,300", value: { D: "credit", C: "debit" } },
  ],
  default: { D: "debit", C: "credit" },
  default_label: "ใช่ D คือเดบิต",
  rememberable: true,
};
const sections: Question = {
  key: "sections",
  code: "sections",
  severity: "cosmetic",
  header: "ส่วนของรายงาน",
  question: "อยากได้ส่วนไหนบ้าง",
  multiSelect: true,
  options: [
    { label: "สรุป", description: "", value: "summary" },
    { label: "กราฟ", description: "", value: "charts" },
    { label: "ภาคผนวก", value: "appendix" },
  ],
  default: ["summary", "charts"],
  default_label: "",
};
const card: CardPayload = { card_id: "c-1", run_id: "r-1", kind: "questions", status: "open", questions: [dc, sections] };

describe("toPendingQuestion", () => {
  it("maps the card to the QuestionCard's shape (promptId = card id, key dropped, descriptions defaulted)", () => {
    expect(toPendingQuestion(card)).toEqual({
      promptId: "c-1",
      questions: [
        {
          question: dc.question,
          header: "เดบิต/เครดิต",
          multiSelect: false,
          options: [
            { label: "ใช่ D คือเดบิต", description: "รายได้รวมจะเป็น 12,480,300" },
            { label: "ไม่ใช่ D คือเครดิต", description: "รายได้รวมจะเป็น -12,480,300" },
          ],
        },
        {
          question: "อยากได้ส่วนไหนบ้าง",
          header: "ส่วนของรายงาน",
          multiSelect: true,
          options: [
            { label: "สรุป", description: "" },
            { label: "กราฟ", description: "" },
            { label: "ภาคผนวก", description: "" },
          ],
        },
      ],
    });
  });

  it("a card without questions is an empty list, not a crash", () => {
    expect(toPendingQuestion({ card_id: "x", questions: null }).questions).toEqual([]);
  });
});

describe("toAnswerBody", () => {
  it("clicked options → {labels} keyed by the question KEY", () => {
    const body = toAnswerBody([dc, sections], {
      promptId: "c-1",
      outcome: "answered",
      answers: { [dc.question]: ["ใช่ D คือเดบิต"], [sections.question]: ["สรุป", "ภาคผนวก"] },
      response: null,
    });
    expect(body).toEqual({ answers: { ambiguous_debit_credit: { labels: ["ใช่ D คือเดบิต"] }, sections: { labels: ["สรุป", "ภาคผนวก"] } } });
  });

  it("'Something else' text (a label that is no option) → {text}", () => {
    const body = toAnswerBody([dc], { promptId: "c-1", outcome: "answered", answers: { [dc.question]: ["D เป็นเครดิตในระบบเรา"] }, response: null });
    expect(body).toEqual({ answers: { ambiguous_debit_credit: { text: "D เป็นเครดิตในระบบเรา" } } });
  });

  it("the free reply → body text", () => {
    const body = toAnswerBody([dc, sections], { promptId: "c-1", outcome: "answered", answers: {}, response: "  ตัดออกหมดเลย  " });
    expect(body).toEqual({ answers: {}, text: "ตัดออกหมดเลย" });
  });

  it("Skip → every question's recommended default, marked as skipped so the report lists it as the agent's choice", () => {
    const body = toAnswerBody([dc, sections], { promptId: "c-1", outcome: "skipped", answers: {}, response: null });
    expect(body).toEqual({
      answers: {
        ambiguous_debit_credit: { labels: ["ใช่ D คือเดบิต"], skipped: true },
        sections: { labels: ["สรุป", "กราฟ"], skipped: true },
      },
    });
  });
});

describe("helpers", () => {
  it("defaultLabels falls back to default_label when the value matches nothing", () => {
    expect(defaultLabels({ ...dc, default: "nope" })).toEqual(["ใช่ D คือเดบิต"]);
    expect(defaultLabels({ ...dc, default: "nope", default_label: "" })).toEqual([]);
  });
  it("isWaitOption is only the string 'wait'", () => {
    expect(isWaitOption("wait")).toBe(true);
    expect(isWaitOption("proceed_without")).toBe(false);
    expect(isWaitOption({ wait: true })).toBe(false);
  });
});
