// AM1 — the clarification card around the app's QuestionCard.
//   * an answer from QuestionCard reaches POST /cards/{id}/answer as
//     {answers:{<key>:{labels}}}; the "Something else" text as {text};
//   * each question's default line and evidence are shown; a re-ask says so;
//   * an answered card is a summary WITHOUT a single button;
//   * a superseded card is one muted line WITHOUT a button;
//   * AM2: a typed value is checked before it is sent; evidence with a page
//     gets a one-line source.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import type { CardPayload, Question } from "../types";
import { AgentQuestionCard } from "./AgentQuestionCard";

const dc: Question = {
  key: "ambiguous_debit_credit",
  code: "ambiguous_debit_credit",
  severity: "blocking",
  header: "เดบิต/เครดิต",
  question: "คอลัมน์ “Dr/Cr” คือเดบิตและเครดิตใช่ไหมครับ",
  options: [
    { label: "ใช่ D คือเดบิต", description: "", value: { D: "debit" } },
    { label: "ไม่ใช่ D คือเครดิต", description: "", value: { D: "credit" } },
  ],
  default: { D: "debit" },
  default_label: "ใช่ D คือเดบิต",
  evidence: { file: "GL.xlsx", column: "Dr/Cr", sample_rows: [[2, "4100", "ขาย", "1000", "C"]], sample_columns: ["แถว", "บัญชี", "คำอธิบาย", "จำนวนเงิน", "Dr/Cr"] },
  rememberable: true,
};
const lang: Question = {
  key: "report_language",
  code: "report_language",
  severity: "cosmetic",
  header: "ภาษา",
  question: "รายงานภาษาอะไรดีครับ",
  options: [
    { label: "ไทย", value: "th" },
    { label: "อังกฤษ", value: "en" },
  ],
  default: "th",
  default_label: "ไทย",
};
const open: CardPayload = { card_id: "card-1", run_id: "run-1", kind: "questions", status: "open", blocking: true, attempts: 0, questions: [dc] };

beforeEach(() => {
  raw.mockReset();
  raw.mockResolvedValue({ ok: true, status: 200, text: async () => JSON.stringify({ changed: true }) });
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

function sentBody(): unknown {
  const call = raw.mock.calls.find((c) => String(c[0]).startsWith("/agent-market/cards/"));
  expect(call, "no POST to /cards/{id}/answer").toBeTruthy();
  expect(call![0]).toBe("/agent-market/cards/card-1/answer");
  expect((call![1] as RequestInit).method).toBe("POST");
  return JSON.parse(String((call![1] as RequestInit).body));
}

describe("AgentQuestionCard (open)", () => {
  it("a clicked option goes out as {answers:{key:{labels}}}", async () => {
    render(<AgentQuestionCard card={open} />);
    fireEvent.click(screen.getAllByTestId("question-option")[0]!);
    fireEvent.click(screen.getByTestId("question-submit"));
    await waitFor(() => expect(raw).toHaveBeenCalled());
    expect(sentBody()).toEqual({ answers: { ambiguous_debit_credit: { labels: ["ใช่ D คือเดบิต"] } } });
    // no second answer can be given while it is being sent / after
    expect(await screen.findByTestId("am-card-pending")).toBeTruthy();
    expect(screen.queryByTestId("question-option")).toBeNull();
  });

  it("'Something else' text goes out as {text}", async () => {
    render(<AgentQuestionCard card={open} />);
    fireEvent.click(screen.getByTestId("question-other"));
    fireEvent.change(screen.getByTestId("question-other-text"), { target: { value: "ในระบบเรา D คือเครดิต" } });
    fireEvent.click(screen.getByTestId("question-submit"));
    await waitFor(() => expect(raw).toHaveBeenCalled());
    expect(sentBody()).toEqual({ answers: { ambiguous_debit_credit: { text: "ในระบบเรา D คือเครดิต" } } });
  });

  it("shows the default line per severity, the evidence expander and the re-ask counter", () => {
    render(<AgentQuestionCard card={{ ...open, attempts: 1, questions: [dc, lang] }} />);
    const lines = screen.getAllByTestId("am-default-line").map((n) => n.textContent);
    expect(lines).toEqual(["ค่าแนะนำ: ใช่ D คือเดบิต — รอคำตอบของคุณก่อนคำนวณ", "ใช้ “ไทย” ไปก่อนแล้ว ตอบภายหลังได้"]);
    expect(screen.getByText("ถามซ้ำครั้งที่ 1 จาก 2")).toBeTruthy();
    const toggle = screen.getByTestId("evidence-toggle");
    expect(toggle.textContent).toBe("ดูตัวอย่าง 1 แถว");
    fireEvent.click(toggle);
    const panel = screen.getByTestId("evidence-panel");
    expect(panel.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(panel.textContent).toContain("GL.xlsx");
  });

  it("a server refusal is shown and the card can be answered again", async () => {
    raw.mockResolvedValue({ ok: false, status: 422, text: async () => JSON.stringify({ error: { code: "ANSWER_REQUIRED", message: "ตอบให้ครบทุกข้อก่อนนะครับ" } }) });
    render(<AgentQuestionCard card={open} />);
    fireEvent.click(screen.getAllByTestId("question-option")[0]!);
    fireEvent.click(screen.getByTestId("question-submit"));
    expect(await screen.findByText("ตอบให้ครบทุกข้อก่อนนะครับ")).toBeTruthy();
    expect((screen.getByTestId("question-submit") as HTMLButtonElement).disabled).toBe(true); // fresh card, nothing chosen yet
    expect(screen.getAllByTestId("question-option")).toHaveLength(2);
  });
});

describe("AgentQuestionCard (closed)", () => {
  it("answered: a summary of header + chosen label + how it was given, and no buttons", () => {
    render(
      <AgentQuestionCard
        card={{
          ...open,
          status: "answered",
          questions: [dc, lang],
          answer: {
            ambiguous_debit_credit: { value: { D: "debit" }, label: "ใช่ D คือเดบิต", via: "card" },
            report_language: { value: "th", label: "ไทย", via: "default" },
          },
        }}
      />,
    );
    const rows = screen.getAllByTestId("am-answer-row").map((r) => r.textContent);
    expect(rows).toEqual(["เดบิต/เครดิตใช่ D คือเดบิตตอบด้วยปุ่ม", "ภาษาไทยใช้ค่าตั้งต้น"]);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByTestId("question-card")).toBeNull();
  });

  it("typed answer shows 'พิมพ์ตอบ'", () => {
    render(<AgentQuestionCard card={{ ...open, status: "answered", answer: { ambiguous_debit_credit: { label: "ใช่ D คือเดบิต", via: "text" } } }} />);
    expect(screen.getByTestId("am-answer-row").textContent).toContain("พิมพ์ตอบ");
  });

  it("superseded: one muted line, no buttons", () => {
    render(<AgentQuestionCard card={{ ...open, status: "superseded" }} />);
    expect(screen.getByTestId("am-card-unused").textContent).toContain("คำถามนี้ไม่ใช้แล้ว");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("interpreting: greyed, says it is reading the answer, no buttons", () => {
    render(<AgentQuestionCard card={{ ...open, status: "interpreting" }} />);
    expect(screen.getByTestId("am-card-pending").textContent).toContain("กำลังอ่านคำตอบของคุณ…");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});

// AM2 — E2 cards: a question with `input` checks the typed value before it
// is sent (the server checks again), and evidence with a page / the text read
// there gets a one-line source.
const total: Question = {
  key: "d1:total",
  code: "e2_low_confidence",
  severity: "blocking",
  header: "ยอดรวม",
  question: "บริษัท ก เลขที่ IV-1 (inv.pdf): อ่านยอดรวมได้ 12,850.70 แต่ไม่มั่นใจพอ ตัวเลขนี้ถูกต้องไหม",
  options: [
    { label: "ถูกต้อง 12,850.70", description: "ยืนยันค่าที่อ่านได้", value: { accept: "12850.70" } },
    { label: "ตัดเอกสารนี้ออก", description: "", value: "exclude" },
  ],
  default: "exclude",
  default_label: "ตัดเอกสารนี้ออก",
  evidence: { document: "inv.pdf", question: "อ่านได้ไม่ชัด", page: 1, bbox: [72.1, 640.5, 180, 652], raw_text: "รวมทั้งสิ้น 12,850.70" },
  input: { kind: "money", hint: "พิมพ์ตัวเลข เช่น 12,850.70" },
};
const buyer: Question = {
  ...total,
  key: "d1:buyer_tax_id",
  header: "เลขผู้ซื้อ",
  question: "อ่านเลขผู้เสียภาษีของผู้ซื้อไม่ได้ ช่วยพิมพ์ค่าที่เห็นในเอกสาร",
  options: [{ label: "ไม่มีในเอกสาร", description: "", value: "absent" }, total.options[1]!],
  evidence: { document: "inv.pdf" },
  input: { kind: "tax_id", hint: "พิมพ์เลข 13 หลัก" },
};
const typedCard: CardPayload = { ...open, card_id: "card-1", questions: [total] };

describe("AgentQuestionCard (typed input, AM2)", () => {
  it("the 'something else' box shows the hint, refuses an invalid amount inline and sends nothing", async () => {
    render(<AgentQuestionCard card={typedCard} />);
    expect(screen.getByTestId("question-card").getAttribute("data-typed")).toBe("true");
    fireEvent.click(screen.getByTestId("question-other"));
    const box = screen.getByTestId("question-other-text") as HTMLInputElement;
    expect(box.placeholder).toBe("พิมพ์ตัวเลข เช่น 12,850.70");
    expect(box.getAttribute("aria-label")).toBe("พิมพ์ตัวเลข เช่น 12,850.70");
    fireEvent.change(box, { target: { value: "12,85" } });
    fireEvent.click(screen.getByTestId("question-submit"));
    expect(screen.getByTestId("am-typed-error").textContent).toBe("ต้องเป็นตัวเลขจำนวนเงิน ทศนิยมไม่เกิน 2 ตำแหน่ง เช่น 12,850.70");
    expect(box.getAttribute("aria-invalid")).toBe("true");
    await new Promise((r) => setTimeout(r, 0));
    expect(raw).not.toHaveBeenCalled();
    expect(screen.queryByTestId("am-card-pending")).toBeNull();

    // fixed: the error goes, and the typed words go out as {text}
    fireEvent.change(box, { target: { value: "๑๒,๘๕๐.๐๐ บาท" } });
    expect(screen.queryByTestId("am-typed-error")).toBeNull();
    fireEvent.click(screen.getByTestId("question-submit"));
    await waitFor(() => expect(raw).toHaveBeenCalled());
    expect(sentBody()).toEqual({ answers: { "d1:total": { text: "๑๒,๘๕๐.๐๐ บาท" } } });
  });

  it("a clicked option on a typed card still goes out as {labels}", async () => {
    render(<AgentQuestionCard card={typedCard} />);
    fireEvent.click(screen.getAllByTestId("question-option")[0]!);
    fireEvent.click(screen.getByTestId("question-submit"));
    await waitFor(() => expect(raw).toHaveBeenCalled());
    expect(sentBody()).toEqual({ answers: { "d1:total": { labels: ["ถูกต้อง 12,850.70"] } } });
  });

  it("a tax ID is checked when the box is left, and Enter does not send it", async () => {
    render(<AgentQuestionCard card={{ ...typedCard, questions: [buyer] }} />);
    fireEvent.click(screen.getByTestId("question-other"));
    const box = screen.getByTestId("question-other-text") as HTMLInputElement;
    expect(box.placeholder).toBe("พิมพ์เลข 13 หลัก");
    fireEvent.change(box, { target: { value: "0105545009870" } });
    expect(screen.queryByTestId("am-typed-error")).toBeNull(); // not while typing
    fireEvent.blur(box);
    expect(screen.getByTestId("am-typed-error").textContent).toBe("เลข 13 หลักนี้ไม่ผ่านการตรวจหลักสุดท้าย ช่วยตรวจอีกครั้ง");
    fireEvent.keyDown(box, { key: "Enter" });
    await new Promise((r) => setTimeout(r, 0));
    expect(raw).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "0-1055-45009-87-5" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(raw).toHaveBeenCalled());
    expect(sentBody()).toEqual({ answers: { "d1:buyer_tax_id": { text: "0-1055-45009-87-5" } } });
  });

  it("the source line: file · page · the text read there", () => {
    render(<AgentQuestionCard card={typedCard} />);
    expect(screen.getByTestId("am-evidence-source").textContent).toBe("ไฟล์ inv.pdf · หน้า 1 · “รวมทั้งสิ้น 12,850.70”");
  });

  it("no source line without a page or raw text (financial evidence)", () => {
    render(<AgentQuestionCard card={{ ...typedCard, questions: [buyer] }} />);
    expect(screen.queryByTestId("am-evidence-source")).toBeNull();
    cleanup();
    render(<AgentQuestionCard card={open} />);
    expect(screen.queryByTestId("am-evidence-source")).toBeNull();
  });

  it("a card without any input: no typed hint, the app's own words for “something else”", () => {
    render(<AgentQuestionCard card={open} />);
    expect(screen.getByTestId("question-card").getAttribute("data-typed")).toBeNull();
    fireEvent.click(screen.getByTestId("question-other"));
    expect((screen.getByTestId("question-other-text") as HTMLInputElement).placeholder).toBe("พิมพ์คำตอบของคุณเอง");
  });

  it("an answer given by typing says so", () => {
    render(<AgentQuestionCard card={{ ...typedCard, status: "answered", answer: { "d1:total": { label: "12,850.00", via: "typed" } } }} />);
    expect(screen.getByTestId("am-answer-row").textContent).toBe("ยอดรวม12,850.00พิมพ์ค่า");
  });
});

// AM4: the live walkthrough in the owner's desktop app (English) showed the
// agent's question card as "The assistant has a question … Something else …
// Reply in your own words instead … Skip · Answer": the shared chat card
// follows the app's language. Inside an agent's workspace it is Thai.
describe("AgentQuestionCard in an English app", () => {
  it("asks in Thai, every word of the card", () => {
    useI18nStore.setState({ locale: "en" });
    render(<AgentQuestionCard card={{ ...open, questions: [dc, lang] }} />);
    const card = screen.getByTestId("am-question-card");
    const text = card.textContent ?? "";
    expect(text).toContain("ผู้ช่วยมีคำถาม");
    expect(text).toContain("อย่างอื่น");
    for (const en of ["The assistant has a question", "Something else", "Reply in your own words", "Skip", "Answer", "choose"]) {
      expect(text).not.toContain(en);
    }
  });
});
