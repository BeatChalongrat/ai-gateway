// FIX132 F12 — a search hit (or #msg-<id>, or "go to the first message")
// opens the chat AT that message: the thread scrolls it to near the top,
// holds it there while rows around it measure, and highlights it briefly.
// NIGHT3 J9: a Ctrl+K hit on message 16 of a 3,000-message chat opened it
// at the bottom. Geometry harness copied from ChatThread.anchor.test.tsx:
//
// jsdom does no layout, so these tests drive a deterministic geometry
// model instead: row i sits at the running sum of mocked heights
// (default 100px), the viewport is 300px, and scrollTop/scrollHeight/
// getBoundingClientRect are overridden to read from that model. What is
// under test is ChatThread's decision-making — where the anchor lands,
// how the spacer breathes, when the hold hands back to stick-to-bottom,
// and that the user's own scroll always wins. (The virtualized-mode DOM
// path and the P2-collapse interplay are exercised in the real-browser
// harness + manual QA; here the "long message" case uses a tall row.)

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The bubble's internals (markdown, voice, router) are irrelevant to
// scroll geometry — replace it with a minimal block.
vi.mock("./MessageBubble", () => ({
  MessageBubble: ({ message }: { message: { content: string } }) => (
    <div>{message.content}</div>
  ),
}));

import type { Message } from "@/lib/types";
import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { dictionaries } from "@/lib/i18n/dict";

import { _resetScrollMemory, ChatThread, JUMP_TOP_PX } from "./ChatThread";

const VIEW_H = 300;
let heights: Record<number, number> = {};
const H = (i: number) => heights[i] ?? 100;
const topOf = (i: number) => {
  let t = 0;
  for (let k = 0; k < i; k++) t += H(k);
  return t;
};

const isScroller = (el: Element) =>
  el.classList?.contains("overflow-y-auto") && el.classList?.contains("h-full");

function spacerOf(scroller: HTMLElement): number {
  const sp = scroller.querySelector<HTMLElement>('[data-testid="turn-spacer"]');
  return sp ? parseFloat(sp.style.height) || 0 : 0;
}

const scrollTops = new WeakMap<Element, number>();

function fakeRect(top: number, height: number): DOMRect {
  return {
    top, height, bottom: top + height,
    left: 0, right: 0, width: 0, x: 0, y: top,
    toJSON() { return {}; },
  } as DOMRect;
}

const origGBCR = HTMLElement.prototype.getBoundingClientRect;

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
  _resetScrollMemory();
  heights = {};
  useChatStore.setState({ turnScrollRequests: {} });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get() { return Math.max(0, Math.min(scrollTops.get(this) ?? 0, this.scrollHeight - VIEW_H)); },
    set(v: number) { scrollTops.set(this, v); },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get() { return isScroller(this) ? VIEW_H : 0; },
  });
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get() {
      if (!isScroller(this)) return 0;
      const rows = this.querySelectorAll("[data-msg-index]").length;
      return Math.max(VIEW_H, topOf(rows) + spacerOf(this as HTMLElement));
    },
  });
  HTMLElement.prototype.getBoundingClientRect = function () {
    if (isScroller(this)) return fakeRect(0, VIEW_H);
    const idxAttr = this.getAttribute?.("data-msg-index");
    if (idxAttr != null) {
      const i = Number(idxAttr);
      const sc = this.closest(".overflow-y-auto") as HTMLElement | null;
      return fakeRect(topOf(i) - (sc ? sc.scrollTop : 0), H(i));
    }
    const parent = this.parentElement;
    if (parent && isScroller(parent) && parent.firstElementChild === this) {
      return fakeRect(-parent.scrollTop, topOf(parent.querySelectorAll("[data-msg-index]").length));
    }
    return fakeRect(0, 0);
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (HTMLElement.prototype as { scrollTop?: unknown }).scrollTop;
  delete (HTMLElement.prototype as { clientHeight?: unknown }).clientHeight;
  delete (HTMLElement.prototype as { scrollHeight?: unknown }).scrollHeight;
  HTMLElement.prototype.getBoundingClientRect = origGBCR;
  cleanup();
});

function msg(
  id: string,
  role: Message["role"],
  content = "x",
  streaming = false,
): Message {
  return {
    id, role, content,
    status: streaming ? "streaming" : "completed",
    created_at: "2026-08-01T10:00:00Z",
    isStreaming: streaming,
  };
}

const seed = (n: number): Message[] =>
  Array.from({ length: n }, (_, i) =>
    msg(`m${i}`, i % 2 === 0 ? "user" : "assistant", `seed ${i}`),
  );


type Jump = { messageId: string; nonce: number } | null;
function mount(initial: Message[], extra: { hasMoreOlder?: boolean; onJumpToFirst?: () => void } = {}) {
  const onJumpHandled = vi.fn();
  const base = { onRegenerate: () => {}, convId: "c1", onJumpHandled, ...extra };
  const view = render(<ChatThread {...base} messages={initial} jumpTo={null} />);
  const scroller = view.container.querySelector(".overflow-y-auto") as HTMLElement;
  return {
    scroller,
    onJumpHandled,
    jump: (jumpTo: Jump, m: Message[] = initial) => act(() => view.rerender(<ChatThread {...base} messages={m} jumpTo={jumpTo} />)),
  };
}

describe("FIX132 F12 — open the chat at a message", () => {
  it("scrolls the message to near the top, highlights it, and reports it found", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const { scroller, jump, onJumpHandled } = mount(seed(30));
      expect(scroller.scrollTop).toBe(3000 - VIEW_H); // opened at the bottom
      jump({ messageId: "m10", nonce: 1 });
      expect(scroller.scrollTop).toBe(topOf(10) - JUMP_TOP_PX);
      expect(onJumpHandled).toHaveBeenCalledWith(1, true);
      const row = scroller.querySelector("#msg-m10") as HTMLElement;
      expect(row.className).toContain("deep-link-target");
      act(() => void vi.advanceTimersByTime(2100));
      expect((scroller.querySelector("#msg-m10") as HTMLElement).className).not.toContain("deep-link-target");
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the message in place when the rows above it turn out taller", () => {
    const { scroller, jump } = mount(seed(30));
    jump({ messageId: "m10", nonce: 1 });
    heights[3] = 260; // a row above measured taller after the jump
    jump({ messageId: "m10", nonce: 1 }, [...seed(30)]);
    expect(scroller.scrollTop).toBe(topOf(10) - JUMP_TOP_PX);
  });

  it("a message that is not in the list reports not found and leaves the view alone", () => {
    const { scroller, jump, onJumpHandled } = mount(seed(30));
    jump({ messageId: "nope", nonce: 2 });
    expect(onJumpHandled).toHaveBeenCalledWith(2, false);
    expect(scroller.scrollTop).toBe(3000 - VIEW_H);
  });

  it("the first message can be reached in one click from the top of the loaded window", () => {
    const onJumpToFirst = vi.fn();
    mount(seed(30), { hasMoreOlder: true, onJumpToFirst });
    fireEvent.click(screen.getByRole("button", { name: dictionaries.en["thread.jumpToFirst"] }));
    expect(onJumpToFirst).toHaveBeenCalledTimes(1);
  });
});
