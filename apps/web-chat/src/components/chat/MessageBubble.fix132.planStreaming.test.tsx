// FIX132 F8 — a document plan streamed as raw JSON. NIGHT3 j3: for ~3.6 s
// of a 7.4-s plan turn the answer showed ":::doc-plan" and the JSON being
// written, until the closing fence let the card mount. While the answer
// streams, a plan fence (open or closed) is now replaced by one quiet line
// — "กำลังเตรียมแผน…" — and the card mounts, as before, when the turn ends.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

import { MessageBubble, streamingWithoutPlans } from "./MessageBubble";

const th = dictionaries.th;
const streaming = (content: string) =>
  ({ id: "a1", role: "assistant", content, status: "streaming", isStreaming: true, created_at: "2026-09-24T00:00:00Z" }) as Message;

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 F8 — no raw plan JSON while the answer streams", () => {
  it("an open doc-plan fence: the prose before it, then the preparing line; no fence, no JSON", () => {
    render(<MessageBubble convId="c1" message={streaming('สรุปจากไฟล์ให้แล้วครับ\n\n:::doc-plan\n{"title": "รายงานสรุป", "sections": [{"heading": "บท')} />);
    const body = screen.getByTestId("assistant-body");
    expect(body.textContent).toContain("สรุปจากไฟล์ให้แล้วครับ");
    expect(body.textContent).not.toContain(":::doc-plan");
    expect(body.textContent).not.toContain('"sections"');
    expect(screen.getByText(th["plan.preparing"])).toBeTruthy();
  });

  it("a closed fence before the turn ends: still no JSON (the card mounts at the end)", () => {
    render(<MessageBubble convId="c1" message={streaming(':::excel-plan\n{"ops": []}\n:::\nเสร็จแล้ว')} />);
    const body = screen.getByTestId("assistant-body");
    expect(body.textContent).not.toContain('"ops"');
    expect(body.textContent).toContain("เสร็จแล้ว");
  });

  it("a half-typed fence opener at the very end is not shown either", () => {
    expect(streamingWithoutPlans("ข้อความ\n\n:::doc-pl")).toEqual({ text: "ข้อความ\n\n", planInProgress: false });
    expect(streamingWithoutPlans("ข้อความธรรมดา")).toEqual({ text: "ข้อความธรรมดา", planInProgress: false });
  });
});
