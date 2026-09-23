// FIX130 C3.4 — attachments never go missing silently.
//
//  F6  a failed chip + text → the message went out without the file and the
//      model said everything arrived. Now any failed chip holds the send and
//      the composer names the file and what to do (retry or remove).
//  F5  "5 chips incl. one error → Send does nothing": the NIGHT2 run had an
//      image at deepseek-v4-flash (no vision); the FIX27 gate held the send
//      with a toast gone within seconds. The composer now says so for as long
//      as the gate holds, with the one-click switch.
//  F4  a name the storage refuses (422 FILENAME_TOO_LONG) is said as such and
//      offers no retry that can never work.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, ApiErr } = vi.hoisted(() => {
  class ApiErr extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return {
    ApiErr,
    mockApi: { uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn(), deleteFile: vi.fn(), deleteFileByClientId: vi.fn() },
  };
});
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: ApiErr }));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }) }));
vi.mock("@/hooks/useWhisperInput", () => ({ useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }) }));

import { clearDraftStore } from "@/lib/draftStore";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ModelEntry } from "@/lib/types";
import { _resetUploadRegistry } from "@/lib/uploadRegistry";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();
const th = dictionaries.th;
const model = (id: string, caps: string[] = []): ModelEntry =>
  ({ id, name: id, provider: "test", description: "", context_window: 128000, capabilities: caps, icon: "", category: "fast", allowed: true }) as ModelEntry;
const TEXT_ONLY = model("deepseek-v4-flash");
const VISION = model("gpt-vision", ["vision"]);

beforeEach(() => {
  vi.clearAllMocks();
  _resetUploadRegistry();
  clearDraftStore();
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [TEXT_ONLY, VISION] });
  mockApi.deleteFile.mockResolvedValue(undefined);
  mockApi.deleteFileByClientId.mockResolvedValue(undefined);
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  clearDraftStore();
  useI18nStore.setState({ locale: "en" });
});

function mount(activeModelId = VISION.id) {
  const onSend = vi.fn();
  const view = render(
    <ChatInput conversationId="c1" disabled={false} streaming={false} onSend={onSend} onStop={vi.fn()} activeModelId={activeModelId} />,
  );
  const textarea = view.container.querySelector("textarea") as HTMLTextAreaElement;
  const fileInput = view.container.querySelector('input[type="file"]') as HTMLInputElement;
  return { view, textarea, fileInput, onSend };
}

async function attach(fileInput: HTMLInputElement, files: File[]) {
  await act(async () => {
    fireEvent.change(fileInput, { target: { files } });
    await new Promise((r) => setTimeout(r, 0));
  });
}

const txt = (name: string) => new File(["hello"], name, { type: "text/plain" });
const chips = () => useChatStore.getState().pendingFiles["c1"] ?? [];

describe("FIX130 C3.4 — a failed chip holds the send and says why", () => {
  it("4 uploaded + 1 failed + text: Enter sends nothing; the composer names the failed file", async () => {
    mockApi.uploadFile.mockImplementation(async (_c: string, f: File) => {
      if (f.name === "bad.txt") throw new ApiErr("INTERNAL_ERROR", "internal error", 500);
      return { id: `srv-${f.name}`, extraction_status: "completed" };
    });
    const m = mount();
    await attach(m.fileInput, [txt("a.txt"), txt("b.txt"), txt("bad.txt"), txt("c.txt"), txt("d.txt")]);
    expect(chips().filter((p) => p.status === "ready")).toHaveLength(4);
    expect(chips().filter((p) => p.status === "error")).toHaveLength(1);

    fireEvent.change(m.textarea, { target: { value: "สรุปไฟล์ทั้งหมด" } });
    fireEvent.keyDown(m.textarea, { key: "Enter" });
    expect(m.onSend).not.toHaveBeenCalled(); // never sent with a file silently missing
    expect(chips()).toHaveLength(5); // and nothing was swept away
    const notice = screen.getByTestId("send-blocked-files");
    expect(notice).toHaveTextContent("bad.txt");
    expect(notice).toHaveTextContent("ลองใหม่");
    expect(screen.getByRole("button", { name: th["input.send"] })).toBeDisabled();

    // Remove the failed one → the four go out, and the person chose that.
    fireEvent.click(screen.getByTestId("remove-failed-files"));
    fireEvent.keyDown(m.textarea, { key: "Enter" });
    expect(m.onSend).toHaveBeenCalledTimes(1);
    expect(m.onSend.mock.calls[0][1]).toEqual(["srv-a.txt", "srv-b.txt", "srv-c.txt", "srv-d.txt"]);
  });

  it("a name the storage refuses: said in Thai, and no retry that can never work", async () => {
    mockApi.uploadFile.mockRejectedValue(new ApiErr("FILENAME_TOO_LONG", "file name too long", 422));
    const m = mount();
    await attach(m.fileInput, [txt("ก".repeat(120) + ".txt")]);
    expect(chips()[0].status).toBe("error");
    expect(chips()[0].error).toBe(th["file.err.nameTooLong"]);
    expect(screen.queryByText(th["file.retry"])).toBeNull();
    expect(screen.getByTestId("send-blocked-files")).toHaveTextContent("เอาไฟล์ออกก่อนส่ง");
  });
});

describe("FIX130 C3.4 — the image the model cannot read is said, not toasted away", () => {
  it("an image at a non-vision model: a standing notice with the one-click switch", async () => {
    mockApi.uploadFile.mockResolvedValue({ id: "srv-img", extraction_status: "completed" });
    const m = mount(TEXT_ONLY.id);
    await attach(m.fileInput, [new File(["png"], "fx-image.png", { type: "image/png" })]);
    fireEvent.change(m.textarea, { target: { value: "อธิบายรูป" } });
    fireEvent.keyDown(m.textarea, { key: "Enter" });
    expect(m.onSend).not.toHaveBeenCalled();
    const notice = screen.getByTestId("send-blocked-vision");
    expect(notice).toHaveTextContent("deepseek-v4-flash");
    expect(notice.querySelector("button")).toHaveTextContent("gpt-vision");
  });
});
