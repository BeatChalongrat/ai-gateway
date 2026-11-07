// AM22 §2.4 — a round with nothing to put in the person's form makes no file, and the form's card says so and why
// (the package's sentence) — never that the form cannot be used.
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

describe("a form the round had nothing for", () => {
  const p: Partial<FormPayload> = {
    name: "ใบกระทบยอด.xlsx",
    status: "failed",
    reason: "nothing_this_round",
    why: "รอบนี้ไม่มียอดคงเหลือของฝั่งธนาคารหรือฝั่งบัญชี จึงไม่มีงบกระทบยอดให้กรอกลงแบบฟอร์ม",
  };

  it("says it was not filled this run, and why — in Thai", () => {
    const card = inThread(p);
    expect(card.textContent).toContain("ไม่ได้กรอกแบบฟอร์มนี้ในรอบนี้");
    expect(card.textContent).toContain("รอบนี้ไม่มียอดคงเหลือ");
    expect(card.textContent).not.toContain("ใช้แบบฟอร์มนี้ไม่ได้");
  });

  it("and in English", () => {
    const card = inThread({ ...p, why: "This round has no closing balance on the bank side or the book side" }, "en");
    expect(card.textContent).toContain("was not filled in this run");
    expect(card.textContent).not.toContain("cannot be used");
  });
});
