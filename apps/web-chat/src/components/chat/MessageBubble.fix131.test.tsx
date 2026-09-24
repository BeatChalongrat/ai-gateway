// FIX131 E1 — a turn that did not finish is shown as not finished, never
// spinning, and offers Retry — live and after a reload.
//
// The server settles such a row (its process went away: a restart, a
// crash, a kill at the end of a stop) as status error, terminal_cause
// interrupted, error_code GENERATION_INTERRUPTED, and keeps the text it had
// streamed. Claude shows "response was interrupted" + Retry, ChatGPT the
// same idea; the Thai reads "คำตอบนี้ไม่เสร็จ" with "ลองใหม่".

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
// Voice output reaches for speechSynthesis, which jsdom lacks.
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import { localizeErrorCode } from "@/lib/errorMessages";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

function interrupted(partial: Partial<Message> = {}): Message {
  return {
    id: "a1",
    role: "assistant",
    content: "ครึ่งแรกของคำตอบ",
    created_at: new Date().toISOString(),
    status: "error",
    error_code: "GENERATION_INTERRUPTED",
    terminal_cause: "interrupted",
    run_id: "run-1",
    ...partial,
  } as Message;
}

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

describe("FIX131 E1 — an interrupted turn", () => {
  it("after a reload (row from the server): 'คำตอบนี้ไม่เสร็จ', the partial text kept, and ลองใหม่ regenerates", () => {
    const onRegenerate = vi.fn();
    render(<MessageBubble convId="c1" message={interrupted()} showRegenerate onRegenerate={onRegenerate} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("คำตอบนี้ไม่เสร็จ");
    expect(screen.getByText("ครึ่งแรกของคำตอบ")).toBeTruthy();
    fireEvent.click(screen.getByTestId("retry-interrupted"));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("retry-interrupted").textContent).toContain("ลองใหม่");
  });

  it("live (the stream's own error frame): the same words and the same Retry", () => {
    const onRegenerate = vi.fn();
    const live = interrupted({ error_message: localizeErrorCode("GENERATION_INTERRUPTED") });
    render(<MessageBubble convId="c1" message={live} showRegenerate onRegenerate={onRegenerate} />);
    expect(screen.getByRole("alert").textContent).toContain("คำตอบนี้ไม่เสร็จ");
    fireEvent.click(screen.getByTestId("retry-interrupted"));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it("an empty interrupted turn is a card, not a spinner", () => {
    render(<MessageBubble convId="c1" message={interrupted({ content: "" })} showRegenerate onRegenerate={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("คำตอบนี้ไม่เสร็จ");
    expect(screen.queryByText(dictionaries.th["gen.working"])).toBeNull();
  });

  it("no Retry where regenerate is not offered (an older answer, or a turn streaming elsewhere)", () => {
    render(<MessageBubble convId="c1" message={interrupted()} />);
    expect(screen.getByRole("alert").textContent).toContain("คำตอบนี้ไม่เสร็จ");
    expect(screen.queryByTestId("retry-interrupted")).toBeNull();
  });

  it("English says it plainly too", () => {
    useI18nStore.setState({ locale: "en" });
    render(<MessageBubble convId="c1" message={interrupted()} showRegenerate onRegenerate={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain(dictionaries.en["error.generationInterrupted"]);
    expect(dictionaries.en["error.generationInterrupted"]).toBe("This answer didn't finish.");
    expect(screen.getByTestId("retry-interrupted").textContent).toContain("Retry");
  });

  it("other failures keep their card without the in-card Retry (unchanged)", () => {
    render(<MessageBubble convId="c1" message={interrupted({ error_code: "provider_timeout", terminal_cause: "timeout" })} showRegenerate onRegenerate={() => {}} />);
    expect(screen.queryByTestId("retry-interrupted")).toBeNull();
  });
});
