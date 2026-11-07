// AM11 (D) — a round's form as a person sees it, for a form with no marker in it.
//
//   * a draft shows the list of what it lacks under its own heading —
//     "ช่องที่ยังกรอกไม่ได้" — each field with why; before, the labels were a row
//     of chips with a heading only a screen reader was told;
//   * a form the agent can barely fill is refused: how many fields the form
//     expects, how many the agent has a value for, which ones it lacks, and
//     the way on (map them yourself) — never a download;
//   * a file with nothing to fill says what the reader recognises;
//   * a form that cannot be used says why in words, not a code;
//   * all of it in English on an English screen.
//
// The card is rendered where a person meets it: inside the thread
// (ThreadView), not on its own — AM10's card passed its own test while the
// thread never drew it.

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";

import type { FormPayload, ThreadItem } from "../types";
import { ThreadView } from "./ThreadView";

vi.mock("../api", async () => {
  const real = await vi.importActual<typeof import("../api")>("../api");
  return { ...real, amApi: { ...real.amApi, runForm: vi.fn(), saveRunForm: vi.fn(), downloadArtifact: vi.fn() } };
});

const RUN = "11111111-1111-4111-8111-111111111111";

function item(p: Partial<FormPayload>): ThreadItem {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    install_id: "33333333-3333-4333-8333-333333333333",
    run_id: RUN,
    kind: "form",
    rev: 7,
    created_at: "2026-10-06T03:00:00Z",
    updated_at: "2026-10-06T03:00:00Z",
    payload: { run_id: RUN, file_id: "44444444-4444-4444-8444-444444444444", name: "form.docx", status: "inspecting", ...p },
  } as unknown as ThreadItem;
}

function inThread(p: Partial<FormPayload>, locale: "th" | "en" = "th") {
  useI18nStore.setState({ locale, hydrated: true });
  render(<ThreadView items={[item(p)]} />);
  return screen.getByTestId("am-form");
}

beforeEach(() => {
  // Modal reads matchMedia for its mobile sheet; jsdom has none
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a draft names what it lacks, under its heading, with why", () => {
  const draft: Partial<FormPayload> = {
    name: "board_cover.xlsx",
    status: "unfilled",
    artifact_id: "55555555-5555-4555-8555-555555555555",
    unfilled: ["Headcount", "Cash at bank (THB)"],
    unfilled_why: ["ยังว่าง — ไม่ได้จับคู่ค่าให้ช่องนี้", "รอบนี้ไม่มีค่าให้ช่องนี้"],
  };

  it("in Thai", () => {
    const card = inThread(draft);
    expect(within(card).getByText("ช่องที่ยังกรอกไม่ได้")).toBeTruthy();
    const list = within(card).getByTestId("am-form-unfilled");
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Headcount — ยังว่าง — ไม่ได้จับคู่ค่าให้ช่องนี้",
      "Cash at bank (THB) — รอบนี้ไม่มีค่าให้ช่องนี้",
    ]);
    expect(card.textContent).toContain("ฉบับร่าง");
    expect(card.textContent).not.toContain("ทุกช่อง");
    expect(within(card).getByTestId("am-form-download").textContent).toBe("ดาวน์โหลดฉบับร่าง");
  });

  it("in English", () => {
    const card = inThread({ ...draft, unfilled_why: ["Still empty — no value was mapped to this field", "This run has no value for this field"] }, "en");
    expect(within(card).getByText("Fields that could not be filled")).toBeTruthy();
    expect(card.textContent).toContain("Headcount — Still empty — no value was mapped to this field");
    expect(card.textContent).toContain("draft");
    expect(card.textContent).not.toMatch(/[฀-๿]/);
  });

  it("a field whose reason the server did not send is still named", () => {
    const card = inThread({ ...draft, unfilled_why: undefined });
    expect(within(within(card).getByTestId("am-form-unfilled")).getAllByRole("listitem").map((r) => r.textContent)).toEqual([
      "Headcount",
      "Cash at bank (THB)",
    ]);
  });
});

describe("a form the agent can barely fill is refused", () => {
  const refused: Partial<FormPayload> = {
    name: "ใบขอซื้อ.docx",
    status: "refused",
    reason: "too_few_fields",
    fields: 12,
    mapped: 1,
    unfilled: ["เลขที่ใบขอซื้อ", "วันที่ขอซื้อ", "ผู้ขอซื้อ"],
    unfilled_why: ["agent นี้ไม่มีค่าสำหรับช่องนี้", "agent นี้ไม่มีค่าสำหรับช่องนี้", "agent นี้ไม่มีค่าสำหรับช่องนี้"],
  };

  it("says how many, names them, offers the way on, and offers no file", () => {
    const card = inThread(refused);
    expect(card.getAttribute("data-status")).toBe("refused");
    const text = within(card).getByTestId("am-form-text").textContent ?? "";
    expect(text).toContain("12");
    expect(text).toContain("1 ช่อง");
    expect(text).toContain("ไม่ถึงครึ่ง");
    expect(within(card).getByText("ช่องที่ยังกรอกไม่ได้")).toBeTruthy();
    expect(within(within(card).getByTestId("am-form-unfilled")).getAllByRole("listitem")).toHaveLength(3);
    expect(within(card).getByTestId("am-form-map")).toBeTruthy();
    expect(within(card).queryByTestId("am-form-download")).toBeNull();
    expect(card.textContent).not.toContain("กรอกแบบฟอร์มให้แล้ว");
  });

  it("in English", () => {
    const card = inThread({ ...refused, name: "purchase_request.docx", unfilled: ["Request no.", "Requested by"], unfilled_why: [] }, "en");
    const text = within(card).getByTestId("am-form-text").textContent ?? "";
    expect(text).toContain("12");
    expect(text).toContain("less than half");
    expect(card.textContent).not.toMatch(/[฀-๿]/);
  });

  it("a file with nothing to fill says what the reader recognises, and offers nothing to map", () => {
    const card = inThread({ name: "letter.docx", status: "refused", reason: "no_fields", unfilled: [] });
    expect(within(card).getByTestId("am-form-text").textContent).toContain("ไม่พบช่องให้กรอก");
    expect(within(card).queryByTestId("am-form-map")).toBeNull();
    expect(within(card).queryByTestId("am-form-unfilled")).toBeNull();
  });
});

describe("a form that cannot be used says why in words", () => {
  it.each([
    ["chart_not_updated", "กราฟ"],
    ["unsupported_location", "เติมไม่ได้"],
  ])("%s", (reason, word) => {
    const card = inThread({ name: "form.docx", status: "failed", reason });
    expect(card.textContent).toContain(word);
    expect(card.textContent).not.toContain(reason);
  });

  it("a reason this page does not know is not printed as a code", () => {
    const card = inThread({ name: "form.docx", status: "failed", reason: "some_new_code" });
    expect(card.textContent).not.toContain("some_new_code");
    expect(within(card).getByTestId("am-form-text").textContent).toContain("ใช้แบบฟอร์มนี้ไม่ได้");
  });
});

describe("a filled form says exactly what was checked", () => {
  it("in both languages", () => {
    const card = inThread({ name: "memo.docx", status: "filled", artifact_id: "55555555-5555-4555-8555-555555555555", unfilled: [] });
    expect(within(card).getByTestId("am-form-text").textContent).toContain("ไม่มีช่องที่ต้องกรอกเหลือว่าง");
    expect(within(card).queryByTestId("am-form-unfilled")).toBeNull();
    cleanup();
    const en = inThread({ name: "memo.docx", status: "filled", artifact_id: "55555555-5555-4555-8555-555555555555", unfilled: [] }, "en");
    expect(within(en).getByTestId("am-form-text").textContent).toContain("no field the form expects is left empty");
  });
});
