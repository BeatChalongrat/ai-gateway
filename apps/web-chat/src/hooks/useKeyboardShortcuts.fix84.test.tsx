// FIX84 row 1 — Ctrl/Cmd+K must open conversation search from anywhere.
//
// Before: the handler looked for the sidebar's search <input>, which is
// only mounted after the search row has been clicked once, and returned
// silently when it was absent — so the advertised shortcut did nothing on
// a fresh page (FIX83 #1, measured on web and desktop).

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockRouter } = vi.hoisted(() => ({ mockRouter: { push: vi.fn(), replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => mockRouter }));

import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";

import { CONV_SEARCH_EVENT } from "@/lib/composerShortcuts";

import { useKeyboardShortcuts } from "./useKeyboardShortcuts";

const chatInitial = useChatStore.getState();

function Harness(props: Parameters<typeof useKeyboardShortcuts>[0] & { withInput?: boolean }) {
  useKeyboardShortcuts(props);
  return props.withInput ? <input data-shortcut="conv-search" defaultValue="abc" /> : null;
}

function key(init: KeyboardEventInit & { key: string }): KeyboardEvent {
  const ev = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  window.dispatchEvent(ev);
  return ev;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("Ctrl/Cmd+K (FIX84 row 1)", () => {
  it("with the search input on screen: focuses and selects it (unchanged behaviour)", () => {
    const onSearch = vi.fn();
    render(<Harness withInput onSearchConversations={onSearch} />);
    const input = document.querySelector<HTMLInputElement>('input[data-shortcut="conv-search"]')!;
    const ev = key({ key: "k", ctrlKey: true });
    expect(ev.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe(3);
    expect(onSearch).not.toHaveBeenCalled();
  });

  it("without the input: asks the layout to show the sidebar, then tells the sidebar to expand search", () => {
    const onSearch = vi.fn();
    const heard = vi.fn();
    window.addEventListener(CONV_SEARCH_EVENT, heard);
    render(<Harness onSearchConversations={onSearch} />);
    const ev = key({ key: "K", metaKey: true });
    expect(ev.defaultPrevented).toBe(true);
    expect(onSearch).toHaveBeenCalledTimes(1);
    // deferred one tick so a freshly mounted sidebar has its listener
    expect(heard).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(heard).toHaveBeenCalledTimes(1);
    window.removeEventListener(CONV_SEARCH_EVENT, heard);
  });

  it("the FIX83 defect: no input, no callback — the event still fires (nothing is silently dropped)", () => {
    const heard = vi.fn();
    window.addEventListener(CONV_SEARCH_EVENT, heard);
    render(<Harness />);
    key({ key: "k", ctrlKey: true });
    vi.runAllTimers();
    expect(heard).toHaveBeenCalledTimes(1);
    window.removeEventListener(CONV_SEARCH_EVENT, heard);
  });

  it("Ctrl+Shift+K is not the search key (Shift combos belong to other bindings)", () => {
    const heard = vi.fn();
    window.addEventListener(CONV_SEARCH_EVENT, heard);
    render(<Harness />);
    key({ key: "K", ctrlKey: true, shiftKey: true });
    vi.runAllTimers();
    expect(heard).not.toHaveBeenCalled();
    window.removeEventListener(CONV_SEARCH_EVENT, heard);
  });
});
