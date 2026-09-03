// FIX50 P1-C — when input DLP masked part of the question, the reply
// carries a quiet expandable notice: the user learns the question was
// altered (counts per detector), never the hidden values. Without this
// the FIX48 ทอง case — the model answering a different question — was
// a silent mystery.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

function msg(partial: Partial<Message>): Message {
  return {
    id: "m1",
    role: "assistant",
    content: "Here is my answer.",
    created_at: new Date().toISOString(),
    status: "completed",
    ...partial,
  } as Message;
}

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});
afterEach(cleanup);

describe("input-masking notice (FIX50 P1-C)", () => {
  const notice = { findings: 3, by_detector: { thai_name: 2, phone: 1 } };

  it("shows the notice when the question was masked", () => {
    render(<MessageBubble convId="c1" message={msg({ dlp_notice: notice })} />);
    expect(screen.getByTestId("dlp-mask-notice")).toBeTruthy();
    expect(
      screen.getByText(dictionaries.en["chat.dlpMasked"].replace("{n}", "3")),
    ).toBeTruthy();
  });

  it("expands to per-detector counts — never values", () => {
    render(<MessageBubble convId="c1" message={msg({ dlp_notice: notice })} />);
    expect(screen.queryByTestId("dlp-mask-detail")).toBeNull();
    fireEvent.click(screen.getByTestId("dlp-mask-notice").querySelector("button")!);
    const detail = screen.getByTestId("dlp-mask-detail");
    expect(detail.textContent).toContain("thai_name × 2");
    expect(detail.textContent).toContain("phone × 1");
  });

  it("absent notice renders nothing", () => {
    render(<MessageBubble convId="c1" message={msg({})} />);
    expect(screen.queryByTestId("dlp-mask-notice")).toBeNull();
  });

  it("the Thai dictionary carries the string too", () => {
    expect(dictionaries.th["chat.dlpMasked"]).toContain("จุด");
  });
});
