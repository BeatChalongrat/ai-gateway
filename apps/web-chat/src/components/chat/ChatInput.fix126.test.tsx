// FIX126 F1 — one per-message file cap on EVERY way a file enters the
// composer (picker, paste, drag-and-drop), counted from the store at the
// moment of staging, with one localized toast that says how many were added
// and how many were left out.
//
// FIX126 F5 — a failed upload chip speaks the UI language: the transport's
// "Network error" and the "Retry" button used to be English in the Thai UI.

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
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isSupported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isSupported: false, isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { ApiError } from "@/lib/api";
import { useToastStore } from "@/components/ui/Toast";
import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { ChatInput, uploadFiles } from "./ChatInput";

const chatInitial = useChatStore.getState();

const txt = (name: string) => new File(["hello"], name, { type: "text/plain" });
const png = (name: string) => new File([new Uint8Array([1, 2, 3])], name, { type: "image/png" });

function mount() {
  const onSend = vi.fn();
  const utils = render(
    <ChatInput conversationId="c1" disabled={false} streaming={false} activeModelId="auto" onSend={onSend} onStop={() => {}} />,
  );
  return { ...utils, onSend, ta: screen.getByRole("textbox") as HTMLTextAreaElement };
}

function pasteImages(el: Element, files: File[]) {
  const ev = new Event("paste", { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "clipboardData", {
    value: {
      items: files.map((f) => ({ kind: "file", type: f.type, getAsFile: () => f })),
      getData: () => "",
    },
  });
  el.dispatchEvent(ev);
}

const chips = (bucket = "c1") => useChatStore.getState().pendingFiles[bucket] ?? [];
const toastText = () => useToastStore.getState().message;

function seedReady(bucket: string, n: number) {
  useChatStore.setState({
    pendingFiles: {
      [bucket]: Array.from({ length: n }, (_, i) => ({
        id: `seed-${i}`,
        file: txt(`seed-${i}.txt`),
        serverId: `srv-seed-${i}`,
        status: "ready" as const,
        progress: 100,
      })),
    },
  });
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

describe("FIX126 F1 — the per-message cap on every entry path", () => {
  it("picker: 3 already attached + 4 picked → 2 added, 2 left out, toast says both numbers", async () => {
    seedReady("c1", 3);
    const { container } = mount();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const picked = [txt("a.txt"), txt("b.txt"), txt("c.txt"), txt("d.txt")];
    await act(async () => {
      fireEvent.change(input, { target: { files: picked } });
    });
    await vi.waitFor(() => expect(mockApi.uploadFile).toHaveBeenCalledTimes(2));
    expect(chips().map((p) => p.file.name)).toEqual(["seed-0.txt", "seed-1.txt", "seed-2.txt", "a.txt", "b.txt"]);
    expect(toastText()).toBe("At most 5 files per message — added 2, left out 2.");
  });

  it("picker in Thai: the same toast is Thai", async () => {
    useI18nStore.setState({ locale: "th" });
    seedReady("c1", 3);
    const { container } = mount();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [txt("a.txt"), txt("b.txt"), txt("c.txt"), txt("d.txt")] } });
    });
    expect(toastText()).toBe("แนบได้สูงสุด 5 ไฟล์ต่อข้อความ — เพิ่ม 2 ไฟล์ ไม่ได้เพิ่ม 2 ไฟล์");
  });

  it("paste: 7 images → 5 attached + toast", async () => {
    const { ta } = mount();
    await act(async () => {
      pasteImages(ta, Array.from({ length: 7 }, (_, i) => png(`p${i}.png`)));
    });
    await vi.waitFor(() => expect(mockApi.uploadFile).toHaveBeenCalledTimes(5));
    expect(chips()).toHaveLength(5);
    expect(toastText()).toBe("At most 5 files per message — added 5, left out 2.");
  });

  it("paste twice: 3 then 4 → the second paste sees the 3 already there (no stale count)", async () => {
    const { ta } = mount();
    await act(async () => {
      pasteImages(ta, [png("a.png"), png("b.png"), png("c.png")]);
    });
    await vi.waitFor(() => expect(chips()).toHaveLength(3));
    await act(async () => {
      pasteImages(ta, [png("d.png"), png("e.png"), png("f.png"), png("g.png")]);
    });
    expect(chips()).toHaveLength(5);
    expect(toastText()).toBe("At most 5 files per message — added 2, left out 2.");
  });

  it("drop (conversation page path): 20 files → 5 attached + toast", async () => {
    const store = useChatStore.getState();
    const files = Array.from({ length: 20 }, (_, i) => txt(`f${i}.txt`));
    await act(async () => {
      await uploadFiles("c1", files, {
        addPendingFile: store.addPendingFile,
        updateFileProgress: store.updateFileProgress,
        markFileReady: store.markFileReady,
        markFileError: store.markFileError,
      });
    });
    expect(mockApi.uploadFile).toHaveBeenCalledTimes(5);
    expect(chips()).toHaveLength(5);
    expect(toastText()).toBe("At most 5 files per message — added 5, left out 15.");
  });

  it("drop with the message already full creates nothing and asks for no conversation", async () => {
    seedReady("__chat_input_draft__", 5);
    const store = useChatStore.getState();
    const resolve = vi.fn(async () => "c-new");
    await act(async () => {
      await uploadFiles(resolve, [txt("x.txt")], {
        addPendingFile: store.addPendingFile,
        updateFileProgress: store.updateFileProgress,
        markFileReady: store.markFileReady,
        markFileError: store.markFileError,
      });
    });
    expect(resolve).not.toHaveBeenCalled();
    expect(mockApi.uploadFile).not.toHaveBeenCalled();
    expect(toastText()).toBe("At most 5 files per message — added 0, left out 1.");
  });
});

describe("FIX126 F5 — the failed upload chip in Thai", () => {
  it("a network failure shows the Thai error and a Thai retry button", async () => {
    useI18nStore.setState({ locale: "th" });
    mockApi.uploadFile.mockRejectedValueOnce(new ApiError("NETWORK_ERROR", "Network error", 0));
    const { container } = mount();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [txt("a.txt")] } });
    });
    await vi.waitFor(() => expect(chips()[0]?.status).toBe("error"));
    expect(screen.queryByText("Network error")).toBeNull();
    expect(screen.queryByText("Retry")).toBeNull();
    expect(screen.getByText("เครือข่ายขัดข้อง — ตรวจสอบการเชื่อมต่อ")).toBeTruthy();
    expect(screen.getByRole("button", { name: "อัปโหลดใหม่" }).textContent).toContain("ลองใหม่");
  });

  it("the drop path's failure is localized too", async () => {
    useI18nStore.setState({ locale: "th" });
    mockApi.uploadFile.mockRejectedValueOnce(new ApiError("HTTP_413", "file too large", 413));
    const store = useChatStore.getState();
    await act(async () => {
      await uploadFiles("c1", [txt("big.txt")], {
        addPendingFile: store.addPendingFile,
        updateFileProgress: store.updateFileProgress,
        markFileReady: store.markFileReady,
        markFileError: store.markFileError,
      });
    });
    expect(chips()[0]?.error).toBe("ไฟล์ใหญ่เกิน 200 MB");
  });
});

describe("FIX126 F3 — a file the server could not read", () => {
  it("extraction_status 'failed' → the chip says it could not be read (TH)", async () => {
    useI18nStore.setState({ locale: "th" });
    mockApi.uploadFile.mockResolvedValueOnce({ id: "srv-bad", filename: "locked.pdf", extraction_status: "failed" });
    const { container } = mount();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File(["%PDF-"], "locked.pdf", { type: "application/pdf" })] } });
    });
    await vi.waitFor(() => expect(chips()[0]?.status).toBe("ready"));
    expect(screen.getByTestId("file-unreadable").textContent).toBe("อ่านไฟล์ไม่ได้ — ไฟล์อาจเสียหายหรือมีรหัสผ่าน");
  });

  it("EN text; 'unsupported' on a PDF counts, on an image it does not", async () => {
    const { FilePreviewCard } = await import("./FilePreviewCard");
    const pending = (name: string, type: string, extractionStatus: "failed" | "unsupported" | "completed") => ({
      id: name, file: new File(["x"], name, { type }), serverId: `s-${name}`, status: "ready" as const, progress: 100, extractionStatus,
    });
    const { rerender } = render(<FilePreviewCard pending={pending("old.pdf", "application/pdf", "unsupported")} onRemove={() => {}} />);
    expect(screen.getByTestId("file-unreadable").textContent).toBe("Couldn't read this file — it may be damaged or password-protected");
    rerender(<FilePreviewCard pending={pending("pic.png", "image/png", "unsupported")} onRemove={() => {}} />);
    expect(screen.queryByTestId("file-unreadable")).toBeNull();
    rerender(<FilePreviewCard pending={pending("ok.pdf", "application/pdf", "completed")} onRemove={() => {}} />);
    expect(screen.queryByTestId("file-unreadable")).toBeNull();
  });
});
