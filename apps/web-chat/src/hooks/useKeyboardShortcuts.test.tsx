// FIX34 Part D — the global bindings Claude has that we lacked, plus the two
// "new chat" keys. Pins: Esc stops only a live stream and only when no
// dialog owns the keyboard; delete asks the caller (which confirms) and only
// with an active chat; copy takes the newest finished assistant reply.

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockRouter } = vi.hoisted(() => ({ mockRouter: { push: vi.fn(), replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => mockRouter }));

import { VOICE_TOGGLE_EVENT } from "@/lib/composerShortcuts";
import { useI18nStore } from "@/lib/i18n";
import type { Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useToastStore } from "@/components/ui/Toast";

import { useKeyboardShortcuts } from "./useKeyboardShortcuts";

const chatInitial = useChatStore.getState();

function Harness(props: Parameters<typeof useKeyboardShortcuts>[0]) {
  useKeyboardShortcuts(props);
  return <textarea data-shortcut="composer" defaultValue="" />;
}

function key(init: KeyboardEventInit & { key: string }): KeyboardEvent {
  const ev = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  window.dispatchEvent(ev);
  return ev;
}

function msg(id: string, role: Message["role"], content: string, extra?: Partial<Message>): Message {
  return { id, role, content, status: "complete", created_at: "2026-08-31T00:00:00Z", ...extra } as Message;
}

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  useToastStore.setState({ message: null, action: null });
});
afterEach(cleanup);

describe("useKeyboardShortcuts (FIX34 Part D)", () => {
  it("Ctrl+Shift+N and Claude's Ctrl+Shift+O both start a new chat", () => {
    render(<Harness />);
    useChatStore.setState({ activeConversationId: "c1" });
    key({ key: "N", ctrlKey: true, shiftKey: true });
    expect(mockRouter.push).toHaveBeenCalledWith("/chat");
    expect(useChatStore.getState().activeConversationId).toBeNull();
    useChatStore.setState({ activeConversationId: "c1" });
    key({ key: "o", metaKey: true, shiftKey: true });
    expect(mockRouter.push).toHaveBeenCalledTimes(2);
    expect(useChatStore.getState().activeConversationId).toBeNull();
  });

  it("Ctrl+Shift+C copies the last finished assistant reply", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    useChatStore.setState({
      activeConversationId: "c1",
      messages: { c1: [msg("u1", "user", "q"), msg("a1", "assistant", "final answer"), msg("a2", "assistant", "typing…", { isStreaming: true })] },
    });
    render(<Harness />);
    key({ key: "C", ctrlKey: true, shiftKey: true });
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith("final answer"));
    expect(useToastStore.getState().message).toBe("Last response copied.");
  });

  it("Ctrl+Shift+C with nothing to copy says so", async () => {
    render(<Harness />);
    key({ key: "c", ctrlKey: true, shiftKey: true });
    await vi.waitFor(() => expect(useToastStore.getState().message).toBe("No response to copy yet."));
  });

  it("Esc stops a live generation; a quiet chat or an open dialog leaves Esc alone", () => {
    const cancelGeneration = vi.fn(async () => {});
    useChatStore.setState({ activeConversationId: "c1", abortControllers: { c1: new AbortController() }, cancelGeneration });
    render(<Harness />);
    const ev = key({ key: "Escape" });
    expect(cancelGeneration).toHaveBeenCalledWith("c1");
    expect(ev.defaultPrevented).toBe(true);

    cancelGeneration.mockClear();
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);
    key({ key: "Escape" });
    expect(cancelGeneration).not.toHaveBeenCalled();
    dialog.remove();

    useChatStore.setState({ abortControllers: {} });
    key({ key: "Escape" });
    expect(cancelGeneration).not.toHaveBeenCalled();
  });

  it("Ctrl+Shift+Backspace asks the caller to confirm, only when a chat is active", () => {
    const onDeleteConversation = vi.fn();
    render(<Harness onDeleteConversation={onDeleteConversation} />);
    key({ key: "Backspace", ctrlKey: true, shiftKey: true });
    expect(onDeleteConversation).not.toHaveBeenCalled();
    useChatStore.setState({ activeConversationId: "c1" });
    key({ key: "Backspace", ctrlKey: true, shiftKey: true });
    expect(onDeleteConversation).toHaveBeenCalledTimes(1);
  });

  it("Ctrl+, opens settings; Ctrl+Shift+V asks the composer to toggle voice; Ctrl+Shift+; focuses the composer", () => {
    const onOpenSettings = vi.fn();
    const onVoice = vi.fn();
    window.addEventListener(VOICE_TOGGLE_EVENT, onVoice);
    render(<Harness onOpenSettings={onOpenSettings} />);
    key({ key: ",", ctrlKey: true });
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    key({ key: "V", ctrlKey: true, shiftKey: true });
    expect(onVoice).toHaveBeenCalledTimes(1);
    const ev = key({ key: ":", code: "Semicolon", ctrlKey: true, shiftKey: true });
    expect(document.activeElement).toBe(document.querySelector('textarea[data-shortcut="composer"]'));
    expect(ev.defaultPrevented).toBe(true);
    window.removeEventListener(VOICE_TOGGLE_EVENT, onVoice);
  });
});
