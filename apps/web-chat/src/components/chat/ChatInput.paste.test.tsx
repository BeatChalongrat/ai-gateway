// FIX89 Part A — a long paste is TEXT: nothing is intercepted, nothing is
// uploaded, nothing is converted, whatever its length. FIX34 turned a paste
// of 5,000+ characters into a .txt attachment (ChatGPT's rule); the Claude
// chat app the owner compared against keeps it in the composer, which grows
// to about half the window and then scrolls inside itself. Pasting an IMAGE
// is unchanged.
//
// FIX34 Part D — ↑ in an EMPTY composer asks the last user message to open
// its edit box; with text present the arrow is not intercepted.

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
import { EDIT_MESSAGE_EVENT } from "@/lib/composerShortcuts";
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

describe("a long paste stays text (FIX89 Part A)", () => {
  it("a 5,000-character paste is left to the browser: no upload, no chip, no toast, not prevented", async () => {
    const { ta } = mount();
    const long = "x".repeat(5_000);
    let ev!: Event;
    await act(async () => {
      ev = paste(ta, long);
    });
    // not intercepted → the browser puts the text in the box itself
    expect(ev.defaultPrevented).toBe(false);
    expect(mockApi.uploadFile).not.toHaveBeenCalled();
    expect(useChatStore.getState().pendingFiles.c1?.length ?? 0).toBe(0);
    expect(useToastStore.getState().message).toBeNull();
  });

  it("50,000 characters are still just text", async () => {
    const { ta } = mount();
    let ev!: Event;
    await act(async () => {
      ev = paste(ta, "y".repeat(50_000));
    });
    expect(ev.defaultPrevented).toBe(false);
    expect(mockApi.uploadFile).not.toHaveBeenCalled();
    expect(useChatStore.getState().pendingFiles.c1?.length ?? 0).toBe(0);
  });

  it("a short paste is not intercepted either", async () => {
    const { ta } = mount();
    let ev!: Event;
    await act(async () => {
      ev = paste(ta, "just a sentence");
    });
    expect(ev.defaultPrevented).toBe(false);
    expect(mockApi.uploadFile).not.toHaveBeenCalled();
  });

  it("pasting an IMAGE still becomes an attachment", async () => {
    const { ta } = mount();
    const png = new File([new Uint8Array([1, 2, 3])], "shot.png", { type: "image/png" });
    let ev!: Event;
    await act(async () => {
      ev = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(ev, "clipboardData", {
        value: {
          items: [{ kind: "file", type: "image/png", getAsFile: () => png }],
          getData: () => "",
        },
      });
      ta.dispatchEvent(ev);
    });
    expect(ev.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(mockApi.uploadFile).toHaveBeenCalledTimes(1));
    expect((mockApi.uploadFile.mock.calls[0][1] as File).name).toBe("shot.png");
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
