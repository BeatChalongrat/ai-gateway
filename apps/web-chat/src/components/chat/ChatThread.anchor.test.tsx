// P3 — send-time turn anchoring.
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

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
import { dictionaries } from "@/lib/i18n/dict";
import { ANCHOR_PREVIOUS_PEEK_PX } from "@/lib/turnAnchor";

import { ChatThread } from "./ChatThread";

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
  heights = {};
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get() { return scrollTops.get(this) ?? 0; },
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
      return topOf(rows) + spacerOf(this as HTMLElement);
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

function mount(initial: Message[], convId = "c1") {
  const props = { onRegenerate: () => {} };
  const view = render(
    <ChatThread {...props} convId={convId} messages={initial} />,
  );
  const scroller = view.container.querySelector(
    ".overflow-y-auto",
  ) as HTMLElement;
  return {
    scroller,
    rerender: (m: Message[], id = convId) =>
      view.rerender(<ChatThread {...props} convId={id} messages={m} />),
  };
}

// 8 seed rows at 100px → content 800, tops i*100. Sending appends the
// user row at index 8 (top 800) + streaming assistant at index 9.
const sent = (extra?: Partial<Record<"userContent", string>>) => [
  ...seed(8),
  msg("u-new", "user", extra?.userContent ?? "my question"),
  msg("a-new", "assistant", "", true),
];

describe("send-time turn anchoring", () => {
  it("lands the sent message's top at the configured peek offset", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());

    // target = max(prevTop 700, 800 - peek) with the default 100px prev.
    expect(scroller.scrollTop).toBe(800 - ANCHOR_PREVIOUS_PEEK_PX);
    // spacer makes it reachable: target + viewport - content(1000).
    expect(spacerOf(scroller)).toBe(800 - ANCHOR_PREVIOUS_PEEK_PX + VIEW_H - 1000);
  });

  it("clamps to the previous message's top when it is shorter than the peek", () => {
    heights[7] = 40; // prev spans 700..740; user row top = 740
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());

    expect(scroller.scrollTop).toBe(700);
  });

  it("anchors even when the user had scrolled up before sending — with no pill", () => {
    const { scroller, rerender } = mount(seed(8));
    scroller.scrollTop = 100;
    fireEvent.scroll(scroller); // autoStick drops: reading old messages

    rerender(sent());

    expect(scroller.scrollTop).toBe(800 - ANCHOR_PREVIOUS_PEEK_PX);
    expect(
      screen.queryByText(dictionaries.en["thread.newMessages"]),
    ).toBeNull();
  });

  it("shrinks the spacer toward zero as the reply grows, then resumes stick-to-bottom", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    const anchored = scroller.scrollTop; // 736
    expect(spacerOf(scroller)).toBe(36);

    heights[9] = 120; // reply grew 100 → 120
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "longer", true)]);
    expect(spacerOf(scroller)).toBe(16);
    expect(scroller.scrollTop).toBe(anchored); // the hold: view does not move

    heights[9] = 300; // reply fills the viewport → handover
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "longer still", true)]);
    expect(spacerOf(scroller)).toBe(0);

    heights[9] = 400; // next delta after handover → stick-to-bottom again
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "even longer", true)]);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
  });

  it("spacer is zero when content below the anchor already exceeds the viewport", () => {
    heights[8] = 400; // a tall (e.g. collapsing-length) sent message
    const { scroller, rerender } = mount(seed(8));
    rerender(sent({ userContent: "very\nlong\nmessage" }));

    expect(scroller.scrollTop).toBe(800 - ANCHOR_PREVIOUS_PEEK_PX);
    expect(spacerOf(scroller)).toBe(0);
  });

  it("a manual user scroll during streaming cancels the hold and the follow", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());

    scroller.scrollTop = 400; // the user drags away mid-stream
    fireEvent.scroll(scroller);

    heights[9] = 250;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "grown", true)]);
    expect(scroller.scrollTop).toBe(400); // nobody moves the user
  });

  // ── Rule 3: opening or switching a conversation lands at the bottom ──

  it("switching conversations lands at the bottom, not a remembered spot", () => {
    const { scroller, rerender } = mount(seed(8));

    // The user reads partway up conversation c1...
    scroller.scrollTop = 200;
    fireEvent.scroll(scroller);
    expect(scroller.scrollTop).toBe(200);

    // ...switches to c2, then back to c1. Both must land at the newest turn.
    rerender(seed(8), "c2");
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);

    rerender(seed(8), "c1");
    expect(scroller.scrollTop).toBe(
      scroller.scrollHeight,
      );
  });

  it("switching after scrolling up restores follow, so no pill is left behind", () => {
    const { scroller, rerender } = mount(seed(8));

    scroller.scrollTop = 100; // scrolled up => autoStick false in c1
    fireEvent.scroll(scroller);

    rerender(seed(8), "c2");

    // Landing at the bottom means streaming in the NEW conversation
    // follows immediately instead of raising "new messages".
    heights[8] = 100;
    rerender([...seed(8), msg("a1", "assistant", "hi", true)], "c2");
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
  });

  it("does not anchor or move on a fresh conversation open", () => {
    const { scroller } = mount(seed(8));
    // default open behaviour: bottom-anchored, no spacer
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    expect(spacerOf(scroller)).toBe(0);
  });
});
