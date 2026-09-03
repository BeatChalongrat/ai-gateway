// FIX50 P1-C put a quiet expandable line under the reply when input
// DLP masked part of the question. FIX51 Part 1 removed the LINE by
// owner decision (OPEN_DECISIONS #62): at ~34% of turns masked the
// sentence was noise, not information. The removal is display-only —
// the ``dlp_notice`` frame still flows end-to-end (chatStore stashes
// it; see chatStore.stream.test.ts) and the server-side counter
// (GET /admin/dlp/input-masking-stats) keeps counting. These tests pin
// the "no line, data intact" contract so the render can't quietly come
// back without a decision, and the transport can't quietly die.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import { useI18nStore } from "@/lib/i18n";
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

describe("under-answer notices removed (FIX51 Part 1 / OPEN_DECISIONS #62)", () => {
  it("a masked question renders NO dlp notice line — even with findings present", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({
          dlp_notice: { findings: 3, by_detector: { thai_name: 2, phone: 1 } },
        })}
      />,
    );
    expect(screen.queryByTestId("dlp-mask-notice")).toBeNull();
    expect(screen.queryByTestId("dlp-mask-detail")).toBeNull();
    // The answer itself still renders — removal is the line only.
    expect(screen.getByText("Here is my answer.")).toBeTruthy();
  });

  it("a reasoning-downgraded reply renders NO under-answer line", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({ reasoning_notice: { model: "x", reason: "no reasoning" } })}
      />,
    );
    expect(
      screen.queryByText(/without extended thinking/i),
    ).toBeNull();
  });

  it("the message type still CARRIES dlp_notice — transport stays alive for the admin counter", () => {
    // Compile-time pin: assigning dlp_notice must remain legal on
    // Message. If the field is deleted from types.ts the counter's
    // upstream frame has lost its typed home and this stops compiling.
    const m: Message = msg({
      dlp_notice: { findings: 1, by_detector: { email: 1 } },
    });
    expect(m.dlp_notice?.findings).toBe(1);
  });
});
