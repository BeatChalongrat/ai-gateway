// AM1 — the clarification card around the app's QuestionCard.
//   * an answer from QuestionCard reaches POST /cards/{id}/answer as
//     {answers:{<key>:{labels}}}; the "Something else" text as {text};
//   * each question's default line and evidence are shown; a re-ask says so;
//   * an answered card is a summary WITHOUT a single button;
//   * a superseded card is one muted line WITHOUT a button.
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
