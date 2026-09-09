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

function mount(initial: Message[], convId = "c1") {
  const props = { onRegenerate: () => {} };
  const view = render(
    <ChatThread {...props} convId={convId} messages={initial} />,
  );
  const scroller = view.container.querySelector(
    ".overflow-y-auto",
  ) as HTMLElement;
  let previous = initial;
  let previousConv = convId;
  return {
    scroller,
    rerender: (m: Message[], id = convId) => act(() => {
      const user = [...m].reverse().find((row) => row.role === "user");
      const oldUser = [...previous].reverse().find((row) => row.role === "user");
      if (id === previousConv && user && user.id !== oldUser?.id) {
        const reply = m[m.length - 1];
        useChatStore.setState({ turnScrollRequests: { [id]: {
          id: reply.id, kind: "send", userKey: user.client_key ?? user.id, replyKey: reply.client_key ?? reply.id,
        } } });
      }
      previous = m;
      previousConv = id;
      view.rerender(<ChatThread {...props} convId={id} messages={m} />);
    }),
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
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
  });

  it("spacer is zero when content below the anchor already exceeds the viewport", () => {
    heights[8] = 400; // a tall (e.g. collapsing-length) sent message
    const { scroller, rerender } = mount(seed(8));
    rerender(sent({ userContent: "very\nlong\nmessage" }));

    // The synchronous animation has finished; an already-full turn hands over.
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
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
  // FIX91 deliberately reverses the retained-spacer policy: completion
  // repays the reservation, and the short reply settling downward is intended.

  it("a short answer releases all reserved space and settles at the real bottom", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());

    const anchored = scroller.scrollTop;
    expect(anchored).toBe(736);
    expect(spacerOf(scroller)).toBeGreaterThan(0);

    // The reply finishes short — one line, nowhere near filling the viewport.
    heights[9] = 40;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "done", false)]);

    // content = 8 seeded rows + the sent message + a 40px reply = 940;
    expect(scroller.scrollTop).toBe(640);
    expect(spacerOf(scroller)).toBe(0);
    expect(screen.queryByRole("button", { name: dictionaries.en["thread.scrollToLatest"] })).toBeNull();
    expect(screen.queryByText(dictionaries.en["thread.newMessages"])).toBeNull();
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
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);

    rerender(seed(8), "c1");
    expect(scroller.scrollTop).toBe(
      scroller.scrollHeight - VIEW_H,
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
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
  });

  it("does not anchor or move on a fresh conversation open", () => {
    const { scroller } = mount(seed(8));
    // default open behaviour: bottom-anchored, no spacer
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
    expect(spacerOf(scroller)).toBe(0);
  });

  it("opening an existing two-row streaming turn creates no send reservation", () => {
    heights[1] = 40;
    const { scroller } = mount([msg("user", "user"), msg("reply", "assistant", "partial", true)]);
    expect(scroller.scrollTop).toBe(0);
    expect(spacerOf(scroller)).toBe(0);
  });

  it("prepending older history preserves the visible row without calling it new content", () => {
    const { scroller, rerender } = mount(seed(8));
    scroller.scrollTop = 200;
    fireEvent.scroll(scroller);
    rerender([msg("older-u", "user"), msg("older-a", "assistant"), ...seed(8)]);
    expect(scroller.scrollTop).toBe(400);
    expect(spacerOf(scroller)).toBe(0);
    expect(screen.queryByText(dictionaries.en["thread.newMessages"])).toBeNull();
    expect(screen.getByRole("button", { name: dictionaries.en["thread.scrollToLatest"] })).toBeTruthy();
  });

  it("a tool wait without a terminal status retains the live reservation", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    rerender([...sent().slice(0, 9), { ...msg("a-new", "assistant", "", false), status: "waiting_user" }]);
    expect(spacerOf(scroller)).toBe(36);
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "", false)]);
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

  /** The thread before a send, parked at the real bottom. */
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
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
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

  it("two sends before queued frames run leave only the newest reservation", () => {
    const { scroller, rerender } = parkedAtBottom();
    rerender(sent());
    const second = [...sent(), msg("u-second", "user"), msg("a-second", "assistant", "", true)];
    rerender(second);
    act(() => flushFrames());
    expect(scroller.scrollTop).toBe(1000 - ANCHOR_PREVIOUS_PEEK_PX);
    expect(spacerOf(scroller)).toBe(36);
    rerender([...second.slice(0, -1), msg("a-second", "assistant", "done", false)]);
    act(() => flushFrames());
    expect(spacerOf(scroller)).toBe(0);
    expect(scroller.scrollTop).toBe(900);
  });

  it("switching away and back invalidates unfinished anchor callbacks", () => {
    const { scroller, rerender } = parkedAtBottom();
    rerender(sent());
    rerender(seed(4), "c2");
    rerender(sent(), "c1");
    act(() => flushFrames());
    expect(spacerOf(scroller)).toBe(0);
    expect(scroller.scrollTop).toBe(700);
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
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
    // Dispatch the scroll event from the bottom-snap.
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
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
    expect(screen.queryByText(dictionaries.en["thread.newMessages"])).toBeNull();
  });

  it("a finished short answer has no reservation left for a reader to scroll into", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    heights[9] = 40;
    rerender([...sent().slice(0, 9), msg("a1", "assistant", "done", false)]);
    expect(spacerOf(scroller)).toBe(0);

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

describe("FIX91 — a completed reservation cannot return after re-layout", () => {
  let fire: (() => void) | null = null;
  beforeEach(() => {
    fire = null;
    vi.stubGlobal("ResizeObserver", class {
      constructor(cb: () => void) { fire = cb; }
      observe() {}
      disconnect() {}
    });
  });

  it("a completed short answer follows a later shrink without recreating its reservation", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    const anchored = scroller.scrollTop; // 736
    heights[9] = 60;
    rerender([...sent().slice(0, 9), msg("a1", "assistant", "done", false)]);
    expect(spacerOf(scroller)).toBe(0);

    // the row re-lays out 20px shorter WITHOUT a render (actions bar,
    // cursor line): the browser clamps scrollTop to the new maximum…
    heights[9] = 40;
    fire!();
    expect(spacerOf(scroller)).toBe(0);
    expect(scroller.scrollTop).toBe(640);
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

// FIX88 Part D — "เขียนลงมาไม่สุด (บางครั้ง)". Measured on the real bundle
// (reports/fix88/scroll-probe.js): the reply that crosses the viewport
// bottom only on its LAST flush handed over and then waited for a next
// delta that never came — the tail sat 435–535 px below the fold with no
// pill. Growth that never arrives as a content delta (the virtualizer's
// late measurement, the finished row's meta/actions lines, a thinking
// block, an image) was likewise never handed over nor followed.
describe("FIX88 — the reply follows to the end", () => {
  let fire: (() => void) | null = null;
  beforeEach(() => {
    fire = null;
    vi.stubGlobal("ResizeObserver", class {
      constructor(cb: () => void) { fire = cb; }
      observe() {}
      disconnect() {}
    });
  });

  it("the handover moves the view to the bottom in the same pass — a final flush that crosses the fold is followed", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    const anchored = scroller.scrollTop; // 736
    heights[9] = 120; // short of the fold: hold
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "a", true)]);
    expect(scroller.scrollTop).toBe(anchored);
    // ONE big last flush: 120 → 700 (well past 736 + 300), then the stream ends
    heights[9] = 700;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "a b c d e f", true)]);
    expect(spacerOf(scroller)).toBe(0);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H); // the OLD code left it at 736
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "a b c d e f", false)]);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
  });

  it("growth that never came as a delta (a resize while holding) hands over by the same rule", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    const anchored = scroller.scrollTop;
    heights[9] = 120;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "a", true)]);
    expect(scroller.scrollTop).toBe(anchored);
    heights[9] = 400; // the row re-lays out taller with NO render (late measurement, highlighting, an image)
    fire!();
    expect(spacerOf(scroller)).toBe(0);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
  });

  it("while following a live reply, a resize keeps the bottom; a short resize while holding still only re-sizes the spacer", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    heights[9] = 400; // past the fold: handover + bottom
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "long", true)]);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);
    // Start the resize with the viewport at the natural bottom.
    scroller.scrollTop = scroller.scrollHeight - VIEW_H;
    heights[9] = 520; // grows without a delta
    fire!();
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);

    // and the hold case that must NOT move: still short after the resize
    cleanup();
    const second = mount(seed(8));
    heights[9] = 100;
    second.rerender(sent());
    const anchored = second.scroller.scrollTop;
    heights[9] = 100;
    second.rerender([...sent().slice(0, 9), msg("a-new", "assistant", "a", true)]);
    heights[9] = 130;
    fire!();
    expect(second.scroller.scrollTop).toBe(anchored);
    expect(spacerOf(second.scroller)).toBe(736 + 300 - 1030);
  });

  it("completion follows final layout without a delta and releases both long and short reservations", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    const anchored = scroller.scrollTop;
    heights[9] = 100; // 900 + 100 < 736 + 300: short, the hold is on
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "same text", true)]);
    expect(scroller.scrollTop).toBe(anchored);
    heights[9] = 420; // the finished row's meta + actions lines: no content change
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "same text", false)]);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight - VIEW_H);

    cleanup();
    heights[9] = 100;
    const short = mount(seed(8));
    short.rerender(sent());
    heights[9] = 60;
    short.rerender([...sent().slice(0, 9), msg("a-new", "assistant", "done", false)]);
    expect(short.scroller.scrollTop).toBe(660);
    expect(spacerOf(short.scroller)).toBe(0);
  });

  it("the reader always wins: scrolled up during the stream, neither a delta nor a resize moves them", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    heights[9] = 400;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "long", true)]);
    scroller.scrollTop = 300; // reads something above
    fireEvent.scroll(scroller);
    heights[9] = 600;
    rerender([...sent().slice(0, 9), msg("a-new", "assistant", "longer", true)]);
    expect(scroller.scrollTop).toBe(300);
    heights[9] = 700;
    fire!();
    expect(scroller.scrollTop).toBe(300);
    expect(screen.getByText(dictionaries.en["thread.newMessages"])).toBeTruthy();
  });
});
