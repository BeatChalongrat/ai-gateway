// FIX130 C4 — the credits pill never covers the last message's timestamp or
// the strip above the composer (NIGHT2 F11 at 360 px, FIX128 §6).
//
// jsdom lays nothing out, so the assertion is geometric on the numbers the
// components actually render: the pill's `bottom` style and the thread's
// bottom padding, measured from the same edge (the top of the stack that sits
// on the composer). The last line of the thread must end at or above the
// pill's top edge.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const usage = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock("@/hooks/useUsage", () => ({ useUsage: () => usage.state }));
vi.mock("@/stores/modelStore", () => ({ useModelStore: (sel: (s: { models: unknown[] }) => unknown) => sel({ models: [] }) }));
vi.mock("./MessageBubble", () => ({ MessageBubble: () => <div data-testid="bubble" /> }));

import type { Message } from "@/lib/types";
import { useComposerStore } from "@/stores/composerStore";

import { ChatThread } from "./ChatThread";
import { CreditBadge } from "./CreditBadge";

const DATA = { credits: { total: 100_000_000, used: 4_875_209, remaining: 95_124_791, reset_date: null }, usage_by_model: [], total_credits_used: 0, period: "lifetime" };
const PILL_H = 36; // jsdom: the pill's fallback height (22 px ring + py-1.5 + border)
const px = (v: string) => Number.parseFloat(v || "0");
const msgs = [
  { id: "u1", role: "user", status: "completed", content: "q", created_at: "2026-09-23T00:00:00Z" },
  { id: "a1", role: "assistant", status: "completed", content: "a", created_at: "2026-09-23T00:00:01Z" },
] as Message[];

beforeEach(() => {
  usage.state = { data: DATA, isError: false, isStale: false, isFetching: false, refetch: vi.fn() };
  // A docked phone composer (96 px) with the long-chat chip + memory strip on top (64 px).
  useComposerStore.setState({ height: 96, above: 64, badge: 0 } as never);
});
afterEach(cleanup);

describe("FIX130 C4 — the credits pill and the thread share the bottom edge honestly", () => {
  it("the pill floats above the stack on the composer, not on it", () => {
    render(<CreditBadge />);
    const pill = screen.getByTestId("credit-badge");
    // 12 px above the TOP of the stack — never over the memory strip.
    expect(px(pill.style.bottom)).toBe(96 + 64 + 12);
  });

  for (const above of [0, 64]) {
    it(`the last line of the thread ends above the pill's top edge (stack on the composer: ${above} px)`, () => {
      useComposerStore.setState({ height: 96, above, badge: 0 } as never);
      render(
        <>
          <ChatThread convId="c1" messages={msgs} onRegenerate={() => {}} />
          <CreditBadge />
        </>,
      );
      const pill = screen.getByTestId("credit-badge");
      const scroller = document.querySelector(".scrollbar-thin.overflow-y-auto") as HTMLElement;
      // An inline padding wins; without one the column's py-6 (24 px) applies.
      const threadBottomPad = px(scroller.style.paddingBottom) || 24;
      // Both measured up from the viewport bottom. The thread column ends at
      // the top of the stack (96 + above); its last line ends
      // `paddingBottom` above that. The pill's top edge is its bottom + its
      // height.
      const lastLineBottom = 96 + above + threadBottomPad;
      const pillTop = px(pill.style.bottom) + PILL_H;
      expect(lastLineBottom).toBeGreaterThanOrEqual(pillTop);
    });
  }

  it("with no pill on screen the thread keeps its ordinary padding", () => {
    usage.state = { data: undefined, isError: false, isStale: false, isFetching: true, refetch: vi.fn() };
    render(
      <>
        <ChatThread convId="c1" messages={msgs} onRegenerate={() => {}} />
        <CreditBadge />
      </>,
    );
    const scroller = document.querySelector(".scrollbar-thin.overflow-y-auto") as HTMLElement;
    expect(px(scroller.style.paddingBottom) || 24).toBe(24);
  });
});
