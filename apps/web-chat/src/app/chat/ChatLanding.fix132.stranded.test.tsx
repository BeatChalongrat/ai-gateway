// FIX132 F25 — "ส่งในแชทใหม่" kept the text but lost the model. NIGHT3
// j5-04/05: a chat on Deepseek V4 Pro was deleted elsewhere; the banner's
// "send in a new chat" sent the text into a new chat on อัตโนมัติ (Auto),
// the new-chat default. The stranded send now remembers the model of the
// chat it was meant for, and the new chat is made with that model.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, mockRouter } = vi.hoisted(() => ({
  mockApi: {
    uploadFile: vi.fn(async (_c: string, f: File) => ({ id: `srv-${f.name}`, filename: f.name, extraction_status: "completed" })),
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
    listSkills: vi.fn(async () => ({ skills: [] })),
    deleteConversationOnExit: vi.fn(),
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

import { SEND_STRANDED_EVENT } from "@/components/ui/ErrorBanner";

describe("FIX132 F25 — the stranded send keeps its model", () => {
  it("send in a new chat → the new chat is made with the deleted chat's model, not Auto", async () => {
    useChatStore.setState({ strandedSend: { text: "สรุปให้หน่อย", modelId: "deepseek-v4-pro" } } as never);
    await act(async () => render(<ChatLanding />));
    await act(async () => {
      window.dispatchEvent(new CustomEvent(SEND_STRANDED_EVENT));
      await new Promise((r) => setTimeout(r, 0));
    });
    await vi.waitFor(() => expect(createConversation).toHaveBeenCalled());
    expect(createConversation).toHaveBeenCalledWith("deepseek-v4-pro");
    expect(sendMessage).toHaveBeenCalledWith("c-new", "สรุปให้หน่อย", [], undefined, undefined);
  });

  it("a stranded send with no model known still goes out on the current pick", async () => {
    useChatStore.setState({ strandedSend: { text: "hi" } } as never);
    await act(async () => render(<ChatLanding />));
    await act(async () => {
      window.dispatchEvent(new CustomEvent(SEND_STRANDED_EVENT));
      await new Promise((r) => setTimeout(r, 0));
    });
    await vi.waitFor(() => expect(createConversation).toHaveBeenCalledWith("auto"));
  });
});
