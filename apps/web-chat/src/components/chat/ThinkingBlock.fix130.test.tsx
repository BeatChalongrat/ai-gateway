// FIX130 B2 — the thinking box shows only when the model really thought,
// and it folds away once the answer is done.
//
//   1. Reasoning that is only whitespace ("\n\n" — an empty think block,
//      or a thought the gateway's DLP / echo guard emptied) is not a
//      thought: no "Analyzed" header over a blank box, live or reloaded.
//   2. A finished row (loaded from history, or re-mounted) starts
//      COLLAPSED. It used to mount expanded and fold 1 s later, so every
//      reasoning row in a reopened chat flashed open and shifted the page.
//   3. The box is collapsible while the model is still thinking — a long
//      qwen thought used to be force-expanded until it ended.
//   4. No "0.0s": a duration measured between a replayed started and
//      completed (resume snapshot) says nothing about how long the model
//      thought, so it is not printed.
//   5. The qwen shape B1 produces (reasoning_delta bracketed by
//      analysis_status, then the answer) lands in the box, not the answer.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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
import { ThinkingBlock } from "./ThinkingBlock";

function msg(partial: Partial<Message>): Message {
  return {
    id: "m1",
    role: "assistant",
    content: "",
    created_at: new Date().toISOString(),
    status: "completed",
    ...partial,
  } as Message;
}

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});
afterEach(cleanup);

const THINKING = dictionaries.en["reasoning.thinking"];
const ANALYZED = dictionaries.en["reasoning.analyzed"];
const header = () => screen.getByText(ANALYZED).closest("button") as HTMLButtonElement;

describe("FIX130 B2 — whitespace is not a thought", () => {
  it.each([
    ["newlines", "\n\n"],
    ["spaces and newlines", "  \n \n"],
  ])("a finished row whose reasoning is only %s shows no thinking box", (_label, reasoning) => {
    render(<MessageBubble convId="c1" message={msg({ content: "5", reasoning_content: reasoning, thinking_duration_ms: 40 })} />);
    expect(screen.queryByText(ANALYZED)).toBeNull();
    expect(screen.queryByText(THINKING)).toBeNull();
    expect(screen.getByText("5")).toBeTruthy();
  });

  it("a finished row with whitespace reasoning and a stale is_thinking shows no box either", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "5", reasoning_content: "\n", is_thinking: true, isStreaming: false })} />);
    expect(screen.queryByText(ANALYZED)).toBeNull();
    expect(screen.queryByText(THINKING)).toBeNull();
  });

  it("while the model is live-thinking with no text yet, the Thinking… header shows (and no empty body)", () => {
    const { container } = render(<ThinkingBlock content={"\n"} thinking />);
    expect(screen.getByText(THINKING)).toBeTruthy();
    expect(container.querySelector(".border-l-2")).toBeNull();
  });
});

describe("FIX130 B2 — collapsed once the answer is done", () => {
  it("a finished row loaded from history starts collapsed and toggles open and shut", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "5", reasoning_content: "step one", thinking_duration_ms: 2500 })} />);
    expect(screen.getByText(ANALYZED)).toBeTruthy();
    expect(screen.queryByText("step one")).toBeNull();
    expect(header().getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(header());
    expect(screen.getByText("step one")).toBeTruthy();
    expect(header().getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(header());
    expect(screen.queryByText("step one")).toBeNull();
  });

  it("a live thinking row can be collapsed by the person while the model is still thinking", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "streaming", isStreaming: true, is_thinking: true, reasoning_content: "step one" })} />);
    expect(screen.getByText("step one")).toBeTruthy();
    const btn = screen.getByText(THINKING).closest("button") as HTMLButtonElement;
    fireEvent.click(btn);
    expect(screen.queryByText("step one")).toBeNull();
    expect(screen.getByText(THINKING)).toBeTruthy();
  });

  it("a live row that finishes thinking folds away 1 s later", () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(<ThinkingBlock content="step one" thinking />);
      expect(screen.getByText("step one")).toBeTruthy();
      rerender(<ThinkingBlock content="step one" thinking={false} durationMs={2500} />);
      expect(screen.getByText("step one")).toBeTruthy();
      act(() => {
        vi.advanceTimersByTime(1100);
      });
      expect(screen.queryByText("step one")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("FIX130 B2 — no 0.0s header", () => {
  it("a near-zero duration (a replayed started/completed pair) prints no time", () => {
    render(<ThinkingBlock content="step one" thinking={false} durationMs={3} />);
    expect(screen.getByText(ANALYZED)).toBeTruthy();
    expect(screen.queryByText(/0\.0/)).toBeNull();
  });

  it("a real duration is still printed", () => {
    render(<ThinkingBlock content="step one" thinking={false} durationMs={2500} />);
    expect(screen.getByText("2.5s")).toBeTruthy();
  });
});

describe("FIX130 B2 — the qwen shape lands in the box, not the answer", () => {
  it("a completed qwen row: thinking under a collapsed Analyzed header, the answer alone in the body", () => {
    const thought = "Thinking Process:\n\n1.  **Analyze the Request:** 2 + 3.\n2.  **Answer:** 5.";
    render(<MessageBubble convId="c1" message={msg({ content: "5 ครับ", reasoning_content: thought, thinking_duration_ms: 1800 })} />);
    expect(screen.getByText("5 ครับ")).toBeTruthy();
    expect(screen.queryByText(/Thinking Process/)).toBeNull();
    fireEvent.click(header());
    expect(screen.getByText(/Thinking Process/)).toBeTruthy();
  });
});
