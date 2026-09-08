// FIX30 §1 — a consent card can never sit where the user cannot see it.
//
// The owner's hang: after a send the thread holds the view on the user's
// message (P3 anchor) and only a TEXT delta or a new row woke the scroller —
// a tool-only turn (plan → sub-agent cards → consent card) grew below the
// fold, the card sat off-screen under the composer, and the turn waited on a
// decision nobody could see. Same geometry model as ChatThread.anchor.test.
//
// Pins:
//   • structural growth (tool cards) counts as content for the hold handover;
//   • a consent arriving while following or still anchored scrolls the tail
//     (where the card is) into view and drops the hold;
//   • a consent arriving while the user has scrolled up on purpose does NOT
//     move the view — it raises the "Permission needed" pill, and the
//     waiting notice + Show button reach the card either way;
//   • the notice names the tool and outranks the stalled notice;
//   • the notice, pill and live-region announcement go away when the card
//     is answered.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./MessageBubble", () => ({
  MessageBubble: ({ message }: { message: { content: string; pending_consent?: { promptId: string } | null } }) => (
    <div>
      {message.content}
      {message.pending_consent ? <div data-testid="consent-card">card</div> : null}
    </div>
  ),
}));

import type { Message } from "@/lib/types";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "@/stores/chatStore";

import { ChatThread } from "./ChatThread";

const VIEW_H = 300;
let heights: Record<number, number> = {};
const H = (i: number) => heights[i] ?? 100;
const topOf = (i: number) => {
  let t = 0;
  for (let k = 0; k < i; k++) t += H(k);
  return t;
};
const isScroller = (el: Element) => el.classList?.contains("overflow-y-auto") && el.classList?.contains("h-full");
const spacerOf = (scroller: HTMLElement) => {
  const sp = scroller.querySelector<HTMLElement>('[data-testid="turn-spacer"]');
  return sp ? parseFloat(sp.style.height) || 0 : 0;
};
const scrollTops = new WeakMap<Element, number>();
const fakeRect = (top: number, height: number): DOMRect =>
  ({ top, height, bottom: top + height, left: 0, right: 0, width: 0, x: 0, y: top, toJSON() { return {}; } }) as DOMRect;
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
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get() { return isScroller(this) ? VIEW_H : 0; } });
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
  useChatStore.setState({ stalledStreams: {}, providerStalledStreams: {} });
  cleanup();
});

function msg(id: string, role: Message["role"], content = "x", streaming = false, extra: Partial<Message> = {}): Message {
  return { id, role, content, status: streaming ? "streaming" : "completed", created_at: "2026-08-01T10:00:00Z", isStreaming: streaming, ...extra };
}
const seed = (n: number): Message[] => Array.from({ length: n }, (_, i) => msg(`m${i}`, i % 2 === 0 ? "user" : "assistant", `seed ${i}`));

function mount(initial: Message[]) {
  const props = { onRegenerate: () => {} };
  const view = render(<ChatThread {...props} convId="c1" messages={initial} />);
  const scroller = view.container.querySelector(".overflow-y-auto") as HTMLElement;
  return { scroller, rerender: (m: Message[]) => view.rerender(<ChatThread {...props} convId="c1" messages={m} />) };
}

const consent = { promptId: "consent-1", tool: "run_command", argumentSummary: "Get-Date", dangerClass: "danger.shell" };
const started = (id: string, tool: string) => ({ id, tool, phase: "started" as const });
const finished = (id: string, tool: string) => ({ id, tool, phase: "finished" as const, success: true });

// 8 seed rows → the send appends user (index 8) + streaming assistant (index 9).
const sent = (asst: Partial<Message> = {}) => [...seed(8), msg("u-new", "user", "run two sub-agents"), msg("a-new", "assistant", "", true, asst)];

describe("FIX30 §1 — the consent card is reachable", () => {
  it("tool cards alone (no text) grow the row and hand the hold over to stick-to-bottom", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    const anchored = scroller.scrollTop;
    expect(anchored).toBeGreaterThan(0);

    // The turn grows by CARDS only — content stays "". Before FIX30 this
    // never woke the scroller; the row could fill the viewport unnoticed.
    heights[9] = 300;
    rerender(sent({ tool_activities: [finished("p", "update_plan"), started("s", "run_subagent")] }));
    expect(spacerOf(scroller)).toBe(0); // handover happened on structural growth
    heights[9] = 420;
    rerender(sent({ tool_activities: [finished("p", "update_plan"), finished("s", "run_subagent"), started("c", "run_command")] }));
    expect(scroller.scrollTop).toBe(scroller.scrollHeight); // following again
  });

  it("consent while still anchored (the owner's shape) → the tail scrolls into view, the notice names the tool", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    const anchored = scroller.scrollTop;
    // FIX88 Part D — 120 px keeps the content short of the viewport at the
    // hold (900 + 120 < 736 + 300); the previous 250 already crossed it,
    // and only the old "snap on the NEXT delta" gap kept the view parked.
    heights[9] = 120; // cards, still short of the viewport → the hold is on
    rerender(sent({ tool_activities: [finished("p", "update_plan"), finished("s", "run_subagent"), started("c", "run_command")] }));
    expect(scroller.scrollTop).toBe(anchored);

    heights[9] = 500; // + the consent card
    rerender(sent({ tool_activities: [finished("p", "update_plan"), finished("s", "run_subagent"), started("c", "run_command")], pending_consent: consent }));
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    expect(spacerOf(scroller)).toBe(0);
    const notice = screen.getByTestId("waiting-consent-notice");
    expect(notice.textContent).toContain("Waiting for your permission — run_command");
    expect(screen.getByRole("button", { name: dictionaries.en["thread.showConsent"] })).toBeTruthy();
    expect(screen.queryByTestId("consent-pill")).toBeNull();
    // The live region announces the wait (a screen-reader user is told too).
    expect(document.querySelector('.sr-only[role="status"]')?.textContent).toContain("Waiting for your permission — run_command");
  });

  it("FIX31 §1.1: a sub-agent's consent → the notice and the announcement name the sub-agent", () => {
    const { rerender } = mount(seed(8));
    rerender(sent());
    heights[9] = 500;
    const subConsent = { ...consent, requestedBy: { kind: "subagent" as const, subagentId: "subagent-1", label: "date", task: "get the date with a shell command" } };
    rerender(sent({ tool_activities: [finished("s", "run_subagent"), started("c", "run_command")], pending_consent: subConsent }));
    const notice = screen.getByTestId("waiting-consent-notice");
    expect(notice.textContent).toContain("Waiting for your permission — run_command, asked by sub-agent “date”");
    expect(document.querySelector('.sr-only[role="status"]')?.textContent).toContain("asked by sub-agent “date”");
    // answered → gone
    rerender(sent({ tool_activities: [finished("s", "run_subagent"), started("c", "run_command")], pending_consent: null }));
    expect(screen.queryByTestId("waiting-consent-notice")).toBeNull();
  });

  it("consent while the user scrolled up on purpose → the view stays, the pill appears, Show reaches the card", () => {
    const { scroller, rerender } = mount(seed(8));
    rerender(sent());
    heights[9] = 250;
    rerender(sent({ tool_activities: [started("c", "run_command")] }));
    scroller.scrollTop = 100;
    fireEvent.scroll(scroller); // reading old messages: autoStick off, anchor off

    heights[9] = 500;
    rerender(sent({ tool_activities: [started("c", "run_command")], pending_consent: consent }));
    expect(scroller.scrollTop).toBe(100); // the user always wins
    const pill = screen.getByTestId("consent-pill");
    expect(pill.textContent).toContain(dictionaries.en["thread.permissionNeeded"]);
    expect(screen.getByTestId("waiting-consent-notice")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: dictionaries.en["thread.showConsent"] }));
    // jsdom has no scrollIntoView → the tail fallback, which is where the card is.
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    expect(screen.queryByTestId("consent-pill")).toBeNull();
  });

  it("the waiting notice outranks the stalled notice, and both go when the card is answered", () => {
    const { rerender } = mount(seed(8));
    rerender(sent());
    useChatStore.setState({ stalledStreams: { c1: true } });
    rerender(sent({ tool_activities: [started("c", "run_command")], pending_consent: consent }));
    expect(screen.getByTestId("waiting-consent-notice")).toBeTruthy();
    expect(screen.queryByTestId("stalled-stream-notice")).toBeNull();

    useChatStore.setState({ stalledStreams: {} });
    rerender(sent({ tool_activities: [finished("c", "run_command")], pending_consent: null }));
    expect(screen.queryByTestId("waiting-consent-notice")).toBeNull();
    expect(screen.queryByTestId("consent-pill")).toBeNull();
  });

  it("a long local tool says which tool and for how long (the host's heartbeat)", () => {
    const { rerender } = mount(seed(8));
    rerender(sent({ tool_activities: [started("c", "run_command")], agent_wait: { kind: "tool", tool: "run_command", elapsedMs: 45_000 } }));
    expect(screen.getByTestId("tool-running-notice").textContent).toContain("Running run_command… 45 s");
    rerender(sent({ tool_activities: [finished("c", "run_command")], agent_wait: null }));
    expect(screen.queryByTestId("tool-running-notice")).toBeNull();
  });
});
