// FIX132 F18 — the composer's file refusals are errors: they use the
// error toast (stays, can be closed, announced as an alert), not the 2-s
// info toast. NIGHT3 f18: the 200 MB refusal vanished at 2.0 s.
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, toastMock, toastErrorMock } = vi.hoisted(() => ({
  mockApi: { uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn(), deleteFile: vi.fn(), deleteFileByClientId: vi.fn() },
  toastMock: vi.fn(),
  toastErrorMock: vi.fn(),
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/components/ui/Toast", () => ({ toast: toastMock, toastError: toastErrorMock }));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }) }));
vi.mock("@/hooks/useWhisperInput", () => ({ useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }) }));

import { clearDraftStore } from "@/lib/draftStore";
import { useI18nStore } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { _resetUploadRegistry } from "@/lib/uploadRegistry";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();
beforeEach(() => {
  vi.clearAllMocks();
  _resetUploadRegistry();
  clearDraftStore();
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [{ id: "m", name: "m", provider: "t", description: "", context_window: 1, capabilities: [], icon: "", category: "fast", allowed: true } as unknown as ModelEntry] });
  window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  clearDraftStore();
  useI18nStore.setState({ locale: "en" });
});

async function attach(files: File[]) {
  const view = render(<ChatInput conversationId="c1" disabled={false} streaming={false} onSend={vi.fn()} onStop={vi.fn()} activeModelId="m" />);
  const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
  await act(async () => {
    fireEvent.change(input, { target: { files } });
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("FIX132 F18 — file refusals are error toasts", () => {
  it("a file over 200 MB", async () => {
    const big = new File(["x"], "night3-too-big.pdf", { type: "application/pdf" });
    Object.defineProperty(big, "size", { value: 201 * 1024 * 1024 });
    await attach([big]);
    expect(toastErrorMock).toHaveBeenCalledWith(expect.stringContaining("night3-too-big.pdf"));
    expect(toastMock).not.toHaveBeenCalledWith(expect.stringContaining("night3-too-big.pdf"));
  });

  it("an upload the server refused", async () => {
    const { ApiError } = await import("@/lib/api");
    mockApi.uploadFile.mockRejectedValue(new (ApiError as unknown as new (c: string, m: string, s: number) => Error)("INTERNAL_ERROR", "boom", 500));
    await attach([new File(["hello"], "a.txt", { type: "text/plain" })]);
    expect(toastErrorMock).toHaveBeenCalled();
  });
});
