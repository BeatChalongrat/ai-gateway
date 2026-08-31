// FIX34 Part D — the composer's two new behaviours:
//   * a long paste (≥ 5,000 chars, ChatGPT's threshold) becomes a .txt
//     attachment with an Undo that puts the text back and drops the chip;
//     a short paste is left to the browser (text lands in the box);
//   * ↑ in an EMPTY composer asks the last user message to open its edit
//     box; with text present the arrow is not intercepted.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    uploadFile: vi.fn(async (_c: string, f: File) => ({ id: `srv-${f.name}`, filename: f.name, extraction_status: "completed" })),
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
    getFileText: vi.fn(),
  },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isSupported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isSupported: false, isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { useToastStore } from "@/components/ui/Toast";
import { EDIT_MESSAGE_EVENT, PASTE_AS_FILE_THRESHOLD } from "@/lib/composerShortcuts";
import { useI18nStore } from "@/lib/i18n";
import type { Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

function msg(id: string, role: Message["role"], content: string): Message {
  return { id, role, content, status: "complete", created_at: "2026-08-31T00:00:00Z" } as unknown as Message;
}

function paste(el: Element, text: string) {
  const data = {
    items: [{ kind: "string", type: "text/plain", getAsFile: () => null }],
    getData: (t: string) => (t === "text/plain" ? text : ""),
  };
  const ev = new Event("paste", { bubbles: true, cancelable: true }) as Event & { clipboardData: unknown };
  Object.defineProperty(ev, "clipboardData", { value: data });
  el.dispatchEvent(ev);
  return ev;
}

function mount() {
  const onSend = vi.fn();
  render(
    <ChatInput conversationId="c1" disabled={false} streaming={false} activeModelId="auto" onSend={onSend} onStop={() => {}} />,
  );
  return { onSend, ta: screen.getByRole("textbox") as HTMLTextAreaElement };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial, activeConversationId: "c1" }, true);
  useModelStore.setState({ models: [], selectedModelId: "auto" });
  useToastStore.setState({ message: null, action: null });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});
afterEach(cleanup);

describe("paste → file (FIX34 Part D)", () => {
  it("a paste at the threshold becomes a .txt chip with an Undo that restores the text", async () => {
    const { ta } = mount();
    const long = "x".repeat(PASTE_AS_FILE_THRESHOLD);
    let ev!: Event;
    await act(async () => {
      ev = paste(ta, long);
    });
    expect(ev.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(mockApi.uploadFile).toHaveBeenCalledTimes(1));
    const uploaded = mockApi.uploadFile.mock.calls[0][1] as File;
    expect(uploaded.name).toMatch(/^pasted-text-\d{8}\.txt$/);
    expect(uploaded.type).toBe("text/plain");
    expect(useChatStore.getState().pendingFiles.c1?.length).toBe(1);
    expect(ta.value).toBe("");
    await vi.waitFor(() => expect(useToastStore.getState().action?.label).toBe("Undo"));
    await act(async () => {
      useToastStore.getState().action!.onClick();
    });
    expect(useChatStore.getState().pendingFiles.c1?.length ?? 0).toBe(0);
    expect(ta.value).toBe(long);
  });

  it("a short paste is not intercepted", async () => {
    const { ta } = mount();
    let ev!: Event;
    await act(async () => {
      ev = paste(ta, "just a sentence");
    });
    expect(ev.defaultPrevented).toBe(false);
    expect(mockApi.uploadFile).not.toHaveBeenCalled();
  });
});

describe("↑ edits the last message (FIX34 Part D)", () => {
  it("fires the edit event for the last user message only when the box is empty", () => {
    useChatStore.setState({ messages: { c1: [msg("u1", "user", "first"), msg("a1", "assistant", "r"), msg("u2", "user", "second")] } });
    const seen: string[] = [];
    const onEdit = (e: Event) => seen.push((e as CustomEvent<{ messageId: string }>).detail.messageId);
    window.addEventListener(EDIT_MESSAGE_EVENT, onEdit);
    const { ta } = mount();
    fireEvent.keyDown(ta, { key: "ArrowUp" });
    expect(seen).toEqual(["u2"]);
    fireEvent.change(ta, { target: { value: "typing" } });
    fireEvent.keyDown(ta, { key: "ArrowUp" });
    expect(seen).toEqual(["u2"]); // not intercepted with text present
    window.removeEventListener(EDIT_MESSAGE_EVENT, onEdit);
  });
});
