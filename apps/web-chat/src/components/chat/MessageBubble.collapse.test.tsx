// P2 — long USER message collapse.
//
// Pins the contract: only user messages taller than the 12-line
// threshold collapse (to 8 lines) with a Show more / Show less toggle;
// short messages and ALL assistant replies render untouched; edit mode
// bypasses the cap; copy always yields the full store content (collapse
// is display-only). Height is mocked per-element because jsdom does no
// layout — the component reads scrollHeight + computed line-height, so
// the tests drive exactly those two.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// Voice output reaches for speechSynthesis, which jsdom lacks.
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import type { Message } from "@/lib/types";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import { MessageBubble } from "./MessageBubble";

const LINE_HEIGHT = 24;
// The component collapses past 12 line-boxes; these mock heights sit
// clearly on either side of the threshold.
const SHORT_LINES = 3;
const LONG_LINES = 40;
const LONG_MARKER = "__LONG__";

function msg(role: Message["role"], content: string): Message {
  return {
    id: "m1",
    role,
    content,
    status: "completed",
    created_at: "2026-08-01T10:00:00Z",
  };
}

const longUserText = `${LONG_MARKER} ` + "ข้อความยาวมาก ".repeat(200);
const shortUserText = "สั้นๆ";

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });

  // jsdom has no layout: scrollHeight is 0 and line-height is "normal".
  // Mock both on the measured element only, keyed off the marker in the
  // content so each test controls whether the message measures "tall".
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: HTMLElement) {
      if (!this.classList?.contains("user-msg-text")) return 0;
      const lines = this.textContent?.includes(LONG_MARKER)
        ? LONG_LINES
        : SHORT_LINES;
      return lines * LINE_HEIGHT;
    },
  });
  const origGCS = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((el, pseudo) => {
    const style = origGCS(el as Element, pseudo as string | null | undefined);
    if ((el as HTMLElement).classList?.contains("user-msg-text")) {
      Object.defineProperty(style, "lineHeight", {
        value: `${LINE_HEIGHT}px`,
        configurable: true,
      });
    }
    return style;
  });
  // Deterministic deferred work (edit-mode focus, collapse anchoring).
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback): number => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn();
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (HTMLElement.prototype as { scrollHeight?: unknown }).scrollHeight;
  cleanup();
});

const showMore = () =>
  screen.queryByRole("button", { name: dictionaries.en["msg.showMore"] });
const showLess = () =>
  screen.queryByRole("button", { name: dictionaries.en["msg.showLess"] });

describe("long user-message collapse", () => {
  it("renders a short user message with no toggle", () => {
    render(<MessageBubble message={msg("user", shortUserText)} convId="c1" />);
    expect(showMore()).toBeNull();
    expect(showLess()).toBeNull();
    expect(document.querySelector(".user-msg-text.is-collapsed")).toBeNull();
  });

  it("collapses a long user message by default with an aria-expanded toggle", () => {
    render(<MessageBubble message={msg("user", longUserText)} convId="c1" />);
    const toggle = showMore();
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector(".user-msg-text.is-collapsed")).not.toBeNull();
  });

  it("expands on click: aria-expanded flips and the label swaps", () => {
    render(<MessageBubble message={msg("user", longUserText)} convId="c1" />);
    fireEvent.click(showMore()!);

    const toggle = showLess();
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(showMore()).toBeNull();
    expect(document.querySelector(".user-msg-text.is-collapsed")).toBeNull();

    // …and collapses back.
    fireEvent.click(toggle!);
    expect(showMore()).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector(".user-msg-text.is-collapsed")).not.toBeNull();
  });

  it("never collapses an assistant message, even with the same long content", () => {
    render(
      <MessageBubble message={msg("assistant", longUserText)} convId="c1" />,
    );
    expect(showMore()).toBeNull();
    expect(showLess()).toBeNull();
    expect(document.querySelector(".user-msg-text")).toBeNull();
  });

  it("edit mode shows the full text with no toggle", () => {
    render(
      <MessageBubble
        message={msg("user", longUserText)}
        convId="c1"
        onEdit={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit message" }));

    const editBox = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(editBox.value).toBe(longUserText);
    expect(showMore()).toBeNull();
    expect(showLess()).toBeNull();
    expect(document.querySelector(".user-msg-text")).toBeNull();
  });

  it("copy on a collapsed message yields the FULL content (display-only cap)", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<MessageBubble message={msg("user", longUserText)} convId="c1" />);
    expect(document.querySelector(".user-msg-text.is-collapsed")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy message" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(longUserText));
  });

  it("has both TH and EN labels, actually translated", () => {
    expect(dictionaries.en["msg.showMore"].length).toBeGreaterThan(0);
    expect(dictionaries.en["msg.showLess"].length).toBeGreaterThan(0);
    expect(dictionaries.th["msg.showMore"].length).toBeGreaterThan(0);
    expect(dictionaries.th["msg.showLess"].length).toBeGreaterThan(0);
    expect(dictionaries.en["msg.showMore"]).not.toBe(
      dictionaries.th["msg.showMore"],
    );
    expect(dictionaries.en["msg.showLess"]).not.toBe(
      dictionaries.th["msg.showLess"],
    );

    // The toggle renders the Thai label under the Thai locale.
    useI18nStore.setState({ locale: "th" });
    render(<MessageBubble message={msg("user", longUserText)} convId="c1" />);
    expect(
      screen.getByRole("button", { name: dictionaries.th["msg.showMore"] }),
    ).toBeInTheDocument();
  });
});
