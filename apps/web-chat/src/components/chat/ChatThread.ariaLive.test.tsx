// B1 — screen-reader live region for the chat thread. Pins:
// (1) a persistent polite role="status" region exists in ChatThread,
// (2) it announces the phase on stream start (Thinking… / Searching…),
// (3) it announces completion on the streaming → done edge,
// (4) it never mirrors streamed content (no token spam), and
// (5) CatIndicator is visual-only (no role="status") so the thread
//     region is the single announcement source.

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

// MessageBubble drags in stores/router/voice — irrelevant to the live
// region; stub it so the thread renders in isolation.
vi.mock("./MessageBubble", () => ({
  MessageBubble: () => <div data-testid="bubble" />,
}));

import { CatIndicator } from "./CatIndicator";
import { ChatThread } from "./ChatThread";

afterEach(cleanup);
beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});

function msg(over: Partial<Message>): Message {
  return {
    id: "m1",
    role: "assistant",
    content: "",
    status: "streaming",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function renderThread(messages: Message[]) {
  return render(
    <ChatThread convId="c1" messages={messages} onRegenerate={() => {}} />,
  );
}

function liveRegion(container: HTMLElement): HTMLElement {
  const el = container.querySelector('[aria-live="polite"][role="status"]');
  expect(el).not.toBeNull();
  return el as HTMLElement;
}

describe("ChatThread live region", () => {
  it("is persistent and announces the waiting label on stream start", () => {
    const { container } = renderThread([msg({ isStreaming: true })]);
    expect(liveRegion(container).textContent).toBe(
      dictionaries.en["gen.working"],
    );
  });

  it("announces the searching phase", () => {
    const { container } = renderThread([
      msg({ isStreaming: true, is_searching: true }),
    ]);
    expect(liveRegion(container).textContent).toBe(
      dictionaries.en["gen.searching"],
    );
  });

  it("announces completion on the streaming → done edge only", () => {
    const streaming = [msg({ isStreaming: true })];
    const { container, rerender } = renderThread(streaming);

    rerender(
      <ChatThread
        convId="c1"
        messages={[msg({ isStreaming: false, status: "completed", content: "hi" })]}
        onRegenerate={() => {}}
      />,
    );
    expect(liveRegion(container).textContent).toBe(
      dictionaries.en["a11y.responseComplete"],
    );
  });

  it("does not announce completion for a thread that never streamed", () => {
    const { container } = renderThread([
      msg({ isStreaming: false, status: "completed", content: "old answer" }),
    ]);
    expect(liveRegion(container).textContent).toBe("");
  });

  it("does not mirror streamed content into the region (no token spam)", () => {
    const { container, rerender } = renderThread([
      msg({ isStreaming: true, content: "partial" }),
    ]);
    rerender(
      <ChatThread
        convId="c1"
        messages={[msg({ isStreaming: true, content: "partial answer grows" })]}
        onRegenerate={() => {}}
      />,
    );
    expect(liveRegion(container).textContent).toBe(
      dictionaries.en["gen.working"],
    );
  });
});

describe("CatIndicator is visual-only", () => {
  it("has no role=status and no aria-label (thread region is the announcer)", () => {
    const { container } = render(
      <CatIndicator labelKey="gen.working" />,
    );
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.getAttribute("role")).toBeNull();
    expect(wrapper.getAttribute("aria-label")).toBeNull();
    expect(container.querySelector('[role="status"]')).toBeNull();
    // The cat svg stays hidden from AT; the visible label remains.
    expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe(
      "true",
    );
  });
});
