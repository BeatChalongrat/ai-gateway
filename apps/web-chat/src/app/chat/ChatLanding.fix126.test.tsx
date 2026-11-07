// FIX126 F2 — attaching on the new-chat page creates the conversation (the
// upload needs its id), but a conversation nothing was sent into is not left
// behind: leaving the page, closing/reloading the tab, or removing every
// attachment deletes it. A send keeps it.
//
// FIX126 F1 — a drop on the new-chat hero is capped like every other path.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, mockRouter } = vi.hoisted(() => ({
  mockApi: {
    uploadFile: vi.fn(async (_c: string, f: File) => ({ id: `srv-${f.name}`, filename: f.name, extraction_status: "completed" })),
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
    listSkills: vi.fn(async () => ({ skills: [] })),
    deleteConversationOnExit: vi.fn(),
    // FIX133 — removing a ready chip deletes its upload on the server
    // (uploadRegistry.discardUpload → api.deleteFile). The mock lacked it, so
    // the click threw "api.deleteFile is not a function" as an unhandled
    // error and the lane exited 1 with no failing test.
    deleteFile: vi.fn(async (_fileId: string) => undefined),
  },
  mockRouter: { push: vi.fn(), replace: vi.fn() },
}));

vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("next/navigation", () => ({ useRouter: () => mockRouter, usePathname: () => "/chat" }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { useToastStore } from "@/components/ui/Toast";
import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import ChatLanding from "./page";

const chatInitial = useChatStore.getState();
const DRAFT = "__chat_input_draft__";
const txt = (name: string) => new File(["hello"], name, { type: "text/plain" });
const chips = () => useChatStore.getState().pendingFiles[DRAFT] ?? [];

let createConversation: ReturnType<typeof vi.fn>;
let deleteConversation: ReturnType<typeof vi.fn>;
let sendMessage: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  useI18nStore.setState({ locale: "en" });
  createConversation = vi.fn(async () => ({ id: "c-new" }));
  deleteConversation = vi.fn(async () => {});
  sendMessage = vi.fn(async () => {});
  useChatStore.setState({ ...chatInitial, createConversation, deleteConversation, sendMessage } as never, true);
  useModelStore.setState({ models: [], selectedModelId: "auto" });
  useToastStore.setState({ message: null, action: null });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});
afterEach(cleanup);

async function mountAndPick(files: File[]) {
  const utils = await act(async () => render(<ChatLanding />));
  const input = utils.container.querySelector('input[type="file"]') as HTMLInputElement;
  await act(async () => {
    fireEvent.change(input, { target: { files } });
  });
  await vi.waitFor(() => expect(mockApi.uploadFile).toHaveBeenCalledTimes(files.length));
  await vi.waitFor(() => expect(chips().every((p) => p.status === "ready")).toBe(true));
  return utils;
}

describe("FIX126 F2 — no empty conversation left behind", () => {
  it("attach, then leave the page without sending → the conversation is deleted and the chips cleared", async () => {
    const { unmount } = await mountAndPick([txt("a.txt")]);
    expect(createConversation).toHaveBeenCalledTimes(1);
    expect(mockApi.uploadFile.mock.calls[0][0]).toBe("c-new");
    unmount();
    await vi.waitFor(() => expect(deleteConversation).toHaveBeenCalledWith("c-new"));
    expect(chips()).toHaveLength(0);
  });

  it("attach, then remove every attachment → the conversation is deleted", async () => {
    await mountAndPick([txt("a.txt"), txt("b.txt")]);
    const removes = screen.getAllByRole("button", { name: "Remove file" });
    await act(async () => {
      fireEvent.click(removes[0]);
    });
    expect(deleteConversation).not.toHaveBeenCalled(); // one is still there
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Remove file" }));
    });
    await vi.waitFor(() => expect(deleteConversation).toHaveBeenCalledWith("c-new"));
    // each removed chip also removes its upload from the server
    expect(mockApi.deleteFile.mock.calls.map((c) => c[0]).sort()).toEqual(["srv-a.txt", "srv-b.txt"]);
  });

  it("attach, then close or reload the tab → a keepalive delete is sent", async () => {
    await mountAndPick([txt("a.txt")]);
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(mockApi.deleteConversationOnExit).toHaveBeenCalledWith("c-new");
  });

  it("attach, then SEND → the conversation is kept, even when the page is left right after", async () => {
    const { unmount } = await mountAndPick([txt("a.txt")]);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "read this" } });
    await act(async () => {
      fireEvent.keyDown(ta, { key: "Enter" });
    });
    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    expect(sendMessage.mock.calls[0][0]).toBe("c-new");
    expect(sendMessage.mock.calls[0][2]).toEqual(["srv-a.txt"]);
    unmount();
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(deleteConversation).not.toHaveBeenCalled();
    expect(mockApi.deleteConversationOnExit).not.toHaveBeenCalled();
  });

  it("nothing attached, nothing sent → nothing created, nothing deleted", async () => {
    const { unmount } = await act(async () => render(<ChatLanding />));
    unmount();
    expect(createConversation).not.toHaveBeenCalled();
    expect(deleteConversation).not.toHaveBeenCalled();
  });
});

describe("FIX126 F1 — a drop on the new-chat hero", () => {
  it("20 files dropped → 5 attached + toast; one conversation", async () => {
    const { container } = await act(async () => render(<ChatLanding />));
    const zone = container.firstElementChild as HTMLElement;
    const files = Array.from({ length: 20 }, (_, i) => txt(`f${i}.txt`));
    await act(async () => {
      fireEvent.drop(zone, { dataTransfer: { files, types: ["Files"] } });
    });
    await vi.waitFor(() => expect(mockApi.uploadFile).toHaveBeenCalledTimes(5));
    expect(chips()).toHaveLength(5);
    expect(createConversation).toHaveBeenCalledTimes(1);
    expect(useToastStore.getState().message).toBe("At most 5 files per message — added 5, left out 15.");
  });
});
