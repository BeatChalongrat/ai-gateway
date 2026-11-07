// FIX127 D3 — a selection in the answer survives the end of the stream.
//
// The completed body is a different element tree from the streaming one,
// so React replaced the selected DOM nodes when isStreaming flipped. The
// body now keeps the streaming renderer while a selection touches it and
// swaps once the selection is released.

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import type { Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { MessageBubble, selectionInside } from "./MessageBubble";

const initial = useChatStore.getState();
beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...initial }, true);
});
afterEach(() => {
  window.getSelection()?.removeAllRanges();
  cleanup();
});

const msg = (over: Partial<Message>): Message =>
  ({ id: "a1", role: "assistant", content: "first paragraph\n\nsecond paragraph", status: "streaming", isStreaming: true, created_at: "2026-01-01T00:00:00Z", ...over }) as Message;

function selectAll(el: HTMLElement) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
}

describe("FIX127 D3 — selection survives stream end", () => {
  it("keeps the streaming body (same DOM) while a selection touches it, swaps when released", () => {
    const view = render(<MessageBubble convId="c1" message={msg({})} />);
    const body = view.getByTestId("assistant-body");
    const node = body.querySelector("p") ?? body;
    selectAll(node as HTMLElement);
    expect(selectionInside(body)).toBe(true);

    view.rerender(<MessageBubble convId="c1" message={msg({ status: "completed", isStreaming: false })} />);

    const held = view.getByTestId("assistant-body");
    expect(held).toBe(body); // same element — nothing was remounted
    expect(held.getAttribute("data-held-for-selection")).toBe("true");
    expect(window.getSelection()!.isCollapsed).toBe(false);

    act(() => {
      window.getSelection()!.removeAllRanges();
      document.dispatchEvent(new Event("selectionchange"));
    });
    expect(view.getByTestId("assistant-body").getAttribute("data-held-for-selection")).toBeNull();
  });

  it("with no selection the body swaps to the completed tree at once", () => {
    const view = render(<MessageBubble convId="c1" message={msg({})} />);
    view.rerender(<MessageBubble convId="c1" message={msg({ status: "completed", isStreaming: false })} />);
    expect(view.getByTestId("assistant-body").getAttribute("data-held-for-selection")).toBeNull();
    expect(view.getByTestId("assistant-body").querySelector(".streaming-body")).toBeNull();
  });
});
