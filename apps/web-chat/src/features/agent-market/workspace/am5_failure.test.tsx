// AM5 phase C — what a person reads when a round does not work, and what
// happens to a document that was left out.
//
//   * The owner's screen showed a failed round with "'NoneType' object is not
//     iterable" in it. No exception text, type name or step id is shown —
//     not from a new round, not from one written before this rule.
//   * A failed round is not a dead end: it says what happened, what to try
//     next, whether the files are safe and whether anything was charged, and
//     offers "ลองใหม่ด้วยไฟล์เดิม", "แจ้งปัญหา" and the round's own page.
//   * The first card a person meets recommended "ตัดเอกสารนี้ออก". A card's
//     recommendation is no longer what skipping it does; every document left
//     out is on the round's "เอกสารที่ยังไม่ได้รวม" list, one click from back.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { retry, restore } = vi.hoisted(() => ({ retry: vi.fn(), restore: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("../api", async (importOriginal) => {
  const m = await importOriginal<typeof import("../api")>();
  return { ...m, amApi: { ...m.amApi, retry, restore, runExceptions: vi.fn().mockResolvedValue({ exceptions: [] }) } };
});

import { useI18nStore } from "@/lib/i18n";

import { reasonText } from "../exceptions/exceptions";
import { tAm } from "../i18n";
import type { CardPayload, Failure, OutputsPayload, Question, ThreadItem } from "../types";
import { AgentQuestionCard } from "./AgentQuestionCard";
import { WorkspaceContext, type WorkspaceCtx } from "./context";
import { OutputsCard } from "./OutputsCard";
import { personText } from "./personText";
import { roundsOf } from "./rounds";
import { ThreadItems } from "./ThreadView";

const RUN = "7cc16723-44ed-40de-919e-f6071a5428e8";
const RAW = "engine step failed: TypeError: 'NoneType' object is not iterable";

const failure: Failure = {
  reason: "render_failed",
  what: "สร้างไฟล์ผลงานไม่สำเร็จ ตัวเลขคำนวณเสร็จแล้วแต่ยังไม่ได้เป็นไฟล์",
  next: "กด “ลองใหม่ด้วยไฟล์เดิม” ได้เลย ถ้ายังไม่สำเร็จอีกให้กด “แจ้งปัญหา” ผู้ดูแลจะเห็นรอบนี้พร้อมขั้นตอนที่หยุด",
  files: "ไฟล์ที่ส่งมาในรอบนี้ยังอยู่ครบและเก็บไว้ให้อีก 7 วัน ผลงานของรอบก่อน ๆ ไม่ถูกแตะต้อง",
  charged: "รอบนี้ใช้ไป 12.5 เครดิต สำหรับขั้นตอนที่ทำเสร็จก่อนจะหยุด ขั้นตอนที่ไม่สำเร็จไม่คิดเครดิต",
  credits: 12.5,
  can_retry: true,
};

function item(kind: string, payload: unknown, id = "it-1", run: string | null = RUN): ThreadItem {
  return { id, kind, rev: 1, run_id: run, created_at: "2026-09-28T10:00:00Z", payload } as unknown as ThreadItem;
}

const merge = vi.fn();
const ws: WorkspaceCtx = { installId: "i-1", merge, threadId: null };
const inWorkspace = (node: React.ReactNode) => render(<WorkspaceContext.Provider value={ws}>{node}</WorkspaceContext.Provider>);

beforeEach(() => {
  retry.mockReset();
  restore.mockReset();
  merge.mockReset();
  useI18nStore.setState({ locale: "th" }); // AM11: the workspace follows the app's language; these tests read it in Thai
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} })) as unknown as typeof window.matchMedia;
});
afterEach(cleanup);

describe("AM5 — a failed round", () => {
  const failed = item("agent_text", { run_id: RUN, tone: "error", text: `${failure.what} — ${failure.next} ${failure.files} ${failure.charged}`, failure });

  it("says what happened, what to try, that the files are safe and what was charged", () => {
    inWorkspace(<ThreadItems items={[failed]} />);
    const card = screen.getByTestId("am-failed-round");
    expect(within(card).getByTestId("am-failed-what").textContent).toBe(failure.what);
    expect(within(card).getByTestId("am-failed-next").textContent).toBe(failure.next);
    expect(within(card).getByTestId("am-failed-files").textContent).toBe(failure.files);
    expect(within(card).getByTestId("am-failed-charged").textContent).toBe(failure.charged);
    // said once: the four parts are not repeated as one paragraph
    expect(card.textContent!.split(failure.files).length).toBe(2);
  });

  it("never shows the reason's code, and is not a dead end", async () => {
    retry.mockResolvedValue({ run: { id: "new-run" }, item: item("progress", { run_id: "new-run", kind: "run", status: "queued" }, "it-9", "new-run") });
    inWorkspace(<ThreadItems items={[failed]} />);
    const card = screen.getByTestId("am-failed-round");
    expect(card.textContent).not.toContain("render_failed");
    expect(card.textContent).not.toMatch(/[A-Za-z]{4,}/);
    expect(within(card).getByTestId("am-failed-run").getAttribute("href")).toBe(`/chat/agents/runs/${RUN}`);
    expect(within(card).getByTestId("am-problem-open")).toBeTruthy();
    fireEvent.click(within(card).getByTestId("am-failed-retry"));
    await waitFor(() => expect(retry).toHaveBeenCalledWith(RUN));
    await waitFor(() => expect(merge).toHaveBeenCalledWith([expect.objectContaining({ id: "it-9" })]));
  });

  it("offers no retry when there is nothing to retry with", () => {
    inWorkspace(<ThreadItems items={[item("agent_text", { run_id: RUN, tone: "error", text: "x", failure: { ...failure, can_retry: false } })]} />);
    expect(screen.queryByTestId("am-failed-retry")).toBeNull();
    expect(screen.getByTestId("am-problem-open")).toBeTruthy();
  });

  it("a round that failed before AM5 is read without the error it was written with", () => {
    const legacy = item("agent_text", {
      run_id: RUN,
      tone: "error",
      text: `รอบนี้ทำไม่สำเร็จครับ (${RAW}) — ส่งไฟล์ใหม่หรือพิมพ์ “ทำรอบใหม่” ไฟล์ที่ส่งมาและผลงานของรอบก่อน ๆ ยังอยู่ครบ`,
    });
    inWorkspace(<ThreadItems items={[legacy]} />);
    const card = screen.getByTestId("am-failed-round");
    expect(card.textContent).toContain("รอบนี้ทำไม่สำเร็จครับ");
    expect(card.textContent).toContain("ยังอยู่ครบ");
    for (const bad of ["TypeError", "NoneType", "iterable", "engine step"]) expect(card.textContent).not.toContain(bad);
  });
});

describe("AM5 — personText", () => {
  it.each([
    [`รอบนี้ทำไม่สำเร็จครับ (${RAW}) — ส่งไฟล์ใหม่`, "รอบนี้ทำไม่สำเร็จครับ — ส่งไฟล์ใหม่"],
    ["engine step failed: KeyError: 'fallback_narrative'", ""],
    ["Traceback (most recent call last):\n  File \"step.py\", line 152", ""],
    ["คำนวณล่วงหน้าไม่สำเร็จ (converter: dial tcp 172.20.0.3:8080: connection refused) Excel จะคำนวณเมื่อเปิดไฟล์", "คำนวณล่วงหน้าไม่สำเร็จ Excel จะคำนวณเมื่อเปิดไฟล์"],
    ["อัปเกรดไม่สำเร็จ: ERROR: deadlock detected (SQLSTATE 40P01)", ""],
    ["compute_failed", ""],
  ])("%s", (raw, want) => {
    expect(personText(raw)).toBe(want);
  });

  it.each(["อ่านไฟล์ 3 ไฟล์ · GL_2569-09.xlsx, bank_statement.xlsx", "เขียนคำอธิบายด้วย qwen3.8-27b-fp8 ที่รันในองค์กร", "ธีม “navy”", "1,284 แถว", failure.what])(
    "leaves what a person can read: %s",
    (s) => {
      expect(personText(s)).toBe(s);
    },
  );
});

describe("AM5 — what a card recommends", () => {
  const card = (q: Partial<Question>): CardPayload =>
    ({
      card_id: "c-1",
      run_id: RUN,
      kind: "questions",
      status: "open",
      questions: [
        {
          key: "e2_check_failed:doc1:vat_7_percent",
          code: "e2_check_failed",
          severity: "blocking",
          header: "VAT ไม่ตรง",
          question: "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf: ภาษีมูลค่าเพิ่มไม่เท่ากับ 7% ของฐาน",
          options: [
            { label: "บันทึกตามเอกสาร มีข้อยกเว้น", value: "accept_as_printed", description: "" },
            { label: "ตัดเอกสารนี้ออก", value: "exclude", description: "" },
          ],
          default: "exclude",
          default_label: "ตัดเอกสารนี้ออก",
          ...q,
        },
      ],
    }) as unknown as CardPayload;

  it("recommends recording the purchase, and says what skipping does", () => {
    render(<AgentQuestionCard card={card({ recommended_label: "บันทึกตามเอกสาร มีข้อยกเว้น", leaves_out: true })} />);
    const line = screen.getByTestId("am-default-line").textContent!;
    expect(line).toContain("ค่าแนะนำ: บันทึกตามเอกสาร มีข้อยกเว้น");
    expect(line).not.toContain("ตัดเอกสารนี้ออก");
    expect(screen.getByTestId("am-skip-line").textContent).toContain("เอกสารที่ยังไม่ได้รวม");
  });

  it("a card that needs the document read recommends nothing — least of all leaving it out", () => {
    render(<AgentQuestionCard card={card({ leaves_out: true })} />);
    expect(screen.queryByTestId("am-default-line")).toBeNull();
    expect(screen.getByTestId("am-skip-line").textContent).toContain("เอกสารที่ยังไม่ได้รวม");
    expect(screen.getByTestId("am-question-card").textContent).not.toContain("ค่าแนะนำ: ตัดเอกสารนี้ออก");
  });

  it("a card of another agent keeps its recommendation", () => {
    render(<AgentQuestionCard card={card({ default_label: "ตัดออก", options: [{ label: "ตัดออก", value: "exclude", description: "" }, { label: "นับรวมด้วย", value: "include", description: "" }] })} />);
    expect(screen.getByTestId("am-default-line").textContent).toContain("ค่าแนะนำ: ตัดออก");
    expect(screen.queryByTestId("am-skip-line")).toBeNull();
  });
});

describe("AM5 — เอกสารที่ยังไม่ได้รวม", () => {
  const outputs = (o: Partial<OutputsPayload> = {}): OutputsPayload => ({
    run_id: RUN,
    period_label: "กันยายน 2569",
    artifacts: [{ id: "a-1", name: "invoice-processing_2569-09_tables_v1.xlsx", format: "xlsx", size: 1024, version: 1 }],
    summary: {
      period_label: "กันยายน 2569",
      not_included: [
        { doc_key: "docvat", name: "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf", reason: "ตรวจตัวเลขไม่ผ่าน", detail: "ภาษีมูลค่าเพิ่มไม่เท่ากับ 7% ของฐาน", how: "นำกลับมารวมได้: บันทึกตามที่เอกสารพิมพ์ พร้อมข้อยกเว้นให้ผู้ตรวจดู", can_restore: true },
        { doc_key: "doctwice", name: "ใบกำกับภาษี ก.ย.69 ร้านเครื่องเขียน.pdf", reason: "ไฟล์เดียวกันส่งมาซ้ำ", detail: "", how: "ไม่ต้องนำกลับ: ไฟล์เดียวกันนี้อยู่ในทะเบียนแล้ว", can_restore: false },
      ],
    },
    ...o,
  });

  it("the files card lists every document left out, why, and brings one back in one click", async () => {
    restore.mockResolvedValue({ changed: true, asks: false, doc_key: "docvat", name: "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf" });
    inWorkspace(<OutputsCard outputs={outputs()} />);
    const list = screen.getByTestId("am-not-included");
    expect(within(list).getByTestId("am-not-included-title").textContent).toBe("เอกสารที่ยังไม่ได้รวม (2)");
    const rows = within(list).getAllByTestId("am-not-included-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain("ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf");
    expect(rows[0]!.textContent).toContain("ตรวจตัวเลขไม่ผ่าน");
    expect(rows[1]!.textContent).toContain("ไฟล์เดียวกันนี้อยู่ในทะเบียนแล้ว");
    expect(within(rows[1]!).queryByTestId("am-restore")).toBeNull();
    fireEvent.click(within(rows[0]!).getByTestId("am-restore"));
    await waitFor(() => expect(restore).toHaveBeenCalledWith(RUN, "docvat"));
    await waitFor(() => expect(within(rows[0]!).getByTestId("am-restore-note").textContent).toContain("กำลังคำนวณใหม่"));
  });

  it("a files card that a newer one replaced lists them without the button", () => {
    inWorkspace(<OutputsCard outputs={outputs()} superseded />);
    expect(screen.getAllByTestId("am-not-included-row")).toHaveLength(2);
    expect(screen.queryByTestId("am-restore")).toBeNull();
  });

  it("nothing left out: no list", () => {
    inWorkspace(<OutputsCard outputs={outputs({ summary: { period_label: "กันยายน 2569", not_included: [] } })} />);
    expect(screen.queryByTestId("am-not-included")).toBeNull();
  });

  it("the round remembers how many are still left out (its newest files card)", () => {
    const items = [
      item("progress", { run_id: RUN, kind: "run", status: "succeeded" }, "p"),
      item("outputs", outputs(), "o1"),
      item("outputs", outputs({ summary: { not_included: [outputs().summary!.not_included![1]] } } as Partial<OutputsPayload>), "o2"),
    ];
    expect(roundsOf(items).get(RUN)!.notIncluded).toBe(1);
  });
});

describe("AM5 — a reason nobody translated", () => {
  it("is a sentence, never its code", () => {
    const t = (k: Parameters<typeof tAm>[0]) => tAm(k);
    expect(reasonText({ reason_code: "some_new_reason", source_ref: {} }, t)).not.toContain("some_new_reason");
    expect(reasonText({ reason_code: "some_new_reason", source_ref: { reason: "เหตุผลของแพ็กเกจ" } }, t)).toBe("เหตุผลของแพ็กเกจ");
  });
});
