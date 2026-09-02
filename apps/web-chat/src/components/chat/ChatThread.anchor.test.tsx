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

  // ── Decision: what happens to the spacer when the turn ends ──
  // It SHRINKS to exactly what still holds the anchored position, and no
  // further. Removing it outright would clamp scrollTop and yank the thread
  // upward the instant a short answer landed — the one thing the anchor
  // exists to prevent. It reaches 0 on its own whenever the reply is long
  // enough to fill the room below the anchor.

  it("a short answer keeps only the spacer that holds the view in place", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());

    const anchored = scroller.scrollTop;
    expect(anchored).toBe(736);
    expect(spacerOf(scroller)).toBeGreaterThan(0);

    // The reply finishes short — one line, nowhere near filling the viewport.
    heights[9] = 40;
    rerender([...sent().slice(0, 9), msg("a1", "assistant", "done", false)]);

    // content = 8 seeded rows + the sent message + a 40px reply = 940;
    // holding scrollTop 736 in a 300px viewport needs exactly 96px.
    expect(scroller.scrollTop).toBe(anchored); // not yanked
    expect(spacerOf(scroller)).toBe(96);
    expect(spacerOf(scroller)).toBeLessThan(164); // smaller than mid-stream
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

// ── FIX47.5 — the handover has to actually happen ──────────────────
// Measured on every send (web and desktop): the anchor jump was driven by
// CSS scroll-behavior:smooth, which keeps raising scroll events for
// ~200 ms after the one-frame `autoScrolling` guard dropped. The first
// of those counted as the person scrolling, cancelled the hold, froze
// the spacer at its send-time size and left follow/no-follow to
// wherever the animation happened to be. These tests drive the frames
// by hand and fire the scroll events a browser would, in between.

describe("FIX47.5 — the anchor jump's own scroll events are not the user's", () => {
  // A frame queue the test drains by hand (the browser's real order), with
  // cancelAnimationFrame honoured — a cancelled jump must not keep moving.
  let frames: Map<number, FrameRequestCallback>;
  let nextId: number;
  let scrollerEl: HTMLElement | null = null;
  /** Run up to n frames. When a frame moved the scroller, the browser
   *  dispatches its scroll event before the next frame's callbacks — so
   *  fire one here too, exactly then. */
  const flushFrames = (n = Infinity) => {
    for (let i = 0; i < n && frames.size > 0; i++) {
      const [id, cb] = frames.entries().next().value as [number, FrameRequestCallback];
      frames.delete(id);
      const before = scrollerEl?.scrollTop;
      cb(0);
      if (scrollerEl && scrollerEl.scrollTop !== before) fireEvent.scroll(scrollerEl);
    }
  };

  beforeEach(() => {
    frames = new Map();
    nextId = 1;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => {
      const id = nextId++;
      frames.set(id, cb);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      frames.delete(id);
    });
  });

  /** The thread as a browser has it before a send: parked at the real
   *  bottom (scrollTop = content − viewport; the fake geometry does not
   *  clamp, so the mount's bottom-snap leaves scrollTop = scrollHeight). */
  function parkedAtBottom() {
    const view = mount(seed(8));
    scrollerEl = view.scroller;
    flushFrames();
    view.scroller.scrollTop = 800 - VIEW_H;
    fireEvent.scroll(view.scroller);
    return view;
  }

  it("the hold survives the jump, the spacer keeps breathing, and the follow resumes on its own", () => {
    const { scroller, rerender } = parkedAtBottom();
    rerender(sent());
    const target = 800 - ANCHOR_PREVIOUS_PEEK_PX;
    // the jump is animated: first frame moves part of the way (and the
    // browser reports it — the scroll event that used to cancel the hold)
    flushFrames(1);
    expect(scroller.scrollTop).toBeGreaterThan(800 - VIEW_H);
    expect(scroller.scrollTop).toBeLessThan(target);
    flushFrames(); // the rest of the jump, one scroll event per frame
    expect(scroller.scrollTop).toBe(target);

    // the reply grows: the hold is still on → spacer shrinks, view stays
    heights[9] = 120;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "longer", true)]);
    expect(scroller.scrollTop).toBe(target);
    expect(spacerOf(scroller)).toBe(16);
    expect(screen.queryByText(dictionaries.en["thread.newMessages"])).toBeNull();

    // the reply fills the viewport → handover, then stick-to-bottom
    heights[9] = 300;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "longer still", true)]);
    expect(spacerOf(scroller)).toBe(0);
    heights[9] = 400;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "even longer", true)]);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    expect(screen.queryByText(dictionaries.en["thread.newMessages"])).toBeNull();
  });

  it("a wheel during the jump is the person's: the jump stops and the hold ends", () => {
    const { scroller, rerender } = parkedAtBottom();
    rerender(sent());
    flushFrames(2);
    const midway = scroller.scrollTop;
    fireEvent.wheel(scroller, { deltaY: -100 });
    scroller.scrollTop = midway - 60;
    fireEvent.scroll(scroller);
    scrollerEl = null; // the jump is cancelled: its frames must not move the view
    flushFrames();
    expect(scroller.scrollTop).toBe(midway - 60);

    heights[9] = 250;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "grown", true)]);
    expect(scroller.scrollTop).toBe(midway - 60); // nobody moves the user
  });

  it("the late-measure re-pass does not restart a jump already heading to the target", () => {
    const { scroller, rerender } = parkedAtBottom();
    rerender(sent());
    const queued = frames.size; // frame 1 of the jump + the re-pass
    flushFrames(1); // frame 1
    flushFrames(1); // the re-pass: same target → no new animation
    expect(frames.size).toBeLessThanOrEqual(queued);
    flushFrames();
    expect(scroller.scrollTop).toBe(800 - ANCHOR_PREVIOUS_PEEK_PX);
  });
});

describe("FIX47.5 — scrolling up mid-stream stops the follow at once", () => {
  function streamingAtBottom() {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    heights[9] = 300; // handover
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "a", true)]);
    heights[9] = 400; // following
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "ab", true)]);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    // the browser clamps a bottom-snap to content − viewport; the fake
    // geometry does not, so park the view where a browser would
    scroller.scrollTop = scroller.scrollHeight - VIEW_H;
    fireEvent.scroll(scroller);
    return { scroller, rerender };
  }

  it("even a small upward scroll (inside the near-bottom band) parks the view and raises the pill", () => {
    const { scroller, rerender } = streamingAtBottom();
    scroller.scrollTop = scroller.scrollHeight - VIEW_H - 40; // a wheel notch up
    fireEvent.scroll(scroller);

    heights[9] = 500;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "abc", true)]);
    expect(scroller.scrollTop).toBe(1300 - VIEW_H - 40); // where the person left it
    expect(screen.getByText(dictionaries.en["thread.newMessages"])).toBeTruthy();
  });

  it("scrolling back to the bottom resumes the follow", () => {
    const { scroller, rerender } = streamingAtBottom();
    scroller.scrollTop = scroller.scrollHeight - VIEW_H - 40;
    fireEvent.scroll(scroller);
    scroller.scrollTop = scroller.scrollHeight - VIEW_H; // back down
    fireEvent.scroll(scroller);

    heights[9] = 500;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "abc", true)]);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    expect(screen.queryByText(dictionaries.en["thread.newMessages"])).toBeNull();
  });

  it("a finished short answer's blank room shrinks as the person scrolls off it", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    heights[9] = 40;
    rerender([...sent().slice(0, 9), msg("a1", "assistant", "done", false)]);
    expect(spacerOf(scroller)).toBe(96); // holds the anchored view

    scroller.scrollTop = 600; // reads something above
    fireEvent.scroll(scroller);
    expect(spacerOf(scroller)).toBe(0); // 600 + 300 <= 940 → nothing to hold
  });
});

describe("FIX47.5 — what must not break", () => {
  it("loading an earlier page keeps the viewport on the same rows (no jump)", async () => {
    const older = Array.from({ length: 4 }, (_, i) =>
      msg(`old${i}`, i % 2 === 0 ? "user" : "assistant", `older ${i}`),
    );
    const props = { onRegenerate: () => {}, hasMoreOlder: true, onLoadEarlier: () => {} };
    const view = render(<ChatThread {...props} convId="c1" messages={seed(8)} />);
    const scroller = view.container.querySelector(".overflow-y-auto") as HTMLElement;

    scroller.scrollTop = 200; // reading near the top
    fireEvent.scroll(scroller);
    fireEvent.click(screen.getByText(dictionaries.en["thread.loadEarlier"]));
    await Promise.resolve();
    // 4 rows × 100px land above → the same content stays under the eye
    view.rerender(<ChatThread {...props} convId="c1" messages={[...older, ...seed(8)]} />);
    expect(scroller.scrollTop).toBe(600);
    expect(spacerOf(scroller)).toBe(0);
  });
});

describe("FIX47.5 — the held position survives the reply's final re-layout", () => {
  let fire: (() => void) | null = null;
  beforeEach(() => {
    fire = null;
    vi.stubGlobal("ResizeObserver", class {
      constructor(cb: () => void) { fire = cb; }
      observe() {}
      disconnect() {}
    });
  });

  it("a finished short answer settling shorter keeps the question where it was", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    const anchored = scroller.scrollTop; // 736
    heights[9] = 60;
    rerender([...sent().slice(0, 9), msg("a1", "assistant", "done", false)]);
    expect(spacerOf(scroller)).toBe(76); // 736 + 300 − 960

    // the row re-lays out 20px shorter WITHOUT a render (actions bar,
    // cursor line): the browser clamps scrollTop to the new maximum…
    heights[9] = 40;
    scroller.scrollTop = 940 - 300 + 76 - 20; // = 696, what a browser would clamp to
    fire!();
    // …and the observer gives the room back and restores the position
    expect(spacerOf(scroller)).toBe(96);
    expect(scroller.scrollTop).toBe(anchored);
  });

  it("once the person has scrolled, a resize changes nothing", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    heights[9] = 60;
    rerender([...sent().slice(0, 9), msg("a1", "assistant", "done", false)]);
    scroller.scrollTop = 500;
    fireEvent.scroll(scroller);
    const spacer = spacerOf(scroller);
    heights[9] = 40;
    fire!();
    expect(scroller.scrollTop).toBe(500);
    expect(spacerOf(scroller)).toBe(spacer);
  });
});
