// Batch 1 W3 — vision-capability gating of image attachments.
//
// Pins the desktop-parity contract (ChatEdgeModels.SupportsVision /
// CurrentModelSupportsVision): a model without vision|image_input|
// multimodal rejects image files on every entry path (picker accept
// list, paste, drag-drop via gateImagesForVision), Auto and unknown
// ids stay optimistic, and an image attached BEFORE a switch to a
// non-vision model is kept — the send is blocked with a toast naming
// the fix rather than the file being dropped.

import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    uploadFile: vi.fn(),
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({
    supported: false,
    isRecording: false,
    start: vi.fn(),
    stop: vi.fn(),
    duration: 0,
  }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({
    isTranscribing: false,
    isRecording: false,
    start: vi.fn(),
    stop: vi.fn(),
    duration: 0,
  }),
}));

import { clearDraftStore } from "@/lib/draftStore";
import { FILE_PICKER_ACCEPT, FILE_PICKER_ACCEPT_NO_IMAGES } from "@/lib/fileUtil";
import { useI18nStore } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { gateImagesForVision } from "@/lib/visionGate";
import { useToastStore } from "@/components/ui/Toast";
import { useChatStore } from "@/stores/chatStore";
import { useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

function model(partial: Partial<ModelEntry> & { id: string }): ModelEntry {
  return {
    name: partial.id,
    provider: "test",
    description: "",
    context_window: 128000,
    capabilities: [],
    icon: "",
    category: "fast" as ModelEntry["category"],
    allowed: true,
    ...partial,
  };
}

const TEXT_ONLY = model({ id: "text-only", name: "Text Only" });
const VISION = model({ id: "gpt-vision", name: "GPT Vision", capabilities: ["vision"] });

function pngFile(name = "cat.png") {
  return new File(["png-bytes"], name, { type: "image/png" });
}

beforeEach(() => {
  vi.clearAllMocks();
  clearDraftStore();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [TEXT_ONLY, VISION] });
  useToastStore.setState({ message: null, nonce: 0 });
  window.matchMedia = ((q: string) => ({
    matches: false,
    media: q,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.stubGlobal(
    "requestAnimationFrame",
    (cb: FrameRequestCallback): number => {
      cb(0);
      return 0;
    },
  );
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  clearDraftStore();
});

function mount(activeModelId: string, onSend = vi.fn()) {
  const props = {
    conversationId: "c1",
    disabled: false,
    streaming: false,
    onSend,
    onStop: vi.fn(),
  };
  const view = render(<ChatInput {...props} activeModelId={activeModelId} />);
  const textarea = view.container.querySelector(
    "textarea",
  ) as HTMLTextAreaElement;
  const fileInput = view.container.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement;
  return {
    view,
    textarea,
    fileInput,
    onSend,
    switchModel: (id: string) =>
      view.rerender(<ChatInput {...props} activeModelId={id} />),
  };
}

function pasteImage(textarea: HTMLTextAreaElement, file: File) {
  fireEvent.paste(textarea, {
    clipboardData: {
      items: [{ kind: "file", type: file.type, getAsFile: () => file }],
    },
  });
}

describe("vision gating", () => {
  it("FIX27: keeps a pasted image on a non-vision model and offers the switch as one click", async () => {
    mockApi.uploadFile.mockResolvedValue({ id: "f1", extraction_status: "completed" });
    const { textarea } = mount(TEXT_ONLY.id);

    await act(async () => pasteImage(textarea, pngFile()));

    // never dropped silently: the file is staged like any other
    expect(mockApi.uploadFile).toHaveBeenCalledTimes(1);
    const st = useToastStore.getState();
    expect(st.message).toContain("GPT Vision");
    expect(st.action?.label).toContain("GPT Vision");
    // the one click switches the model
    act(() => st.action!.onClick());
    expect(useModelStore.getState().selectedModelId).toBe(VISION.id);
  });

  it("lets pasted images through on a vision model", async () => {
    mockApi.uploadFile.mockResolvedValue({ id: "f1", extraction_status: "completed" });
    const { textarea } = mount(VISION.id);

    await act(async () => pasteImage(textarea, pngFile()));

    expect(mockApi.uploadFile).toHaveBeenCalledTimes(1);
  });

  it("switches the picker accept list off images for a non-vision model", () => {
    const nonVision = mount(TEXT_ONLY.id);
    expect(nonVision.fileInput.getAttribute("accept")).toBe(
      FILE_PICKER_ACCEPT_NO_IMAGES,
    );
    expect(nonVision.fileInput.getAttribute("accept")).not.toContain("image/png");
    nonVision.view.unmount();

    const vision = mount(VISION.id);
    expect(vision.fileInput.getAttribute("accept")).toBe(FILE_PICKER_ACCEPT);
  });

  it("keeps an already-attached image after a switch to a non-vision model and blocks send with a toast", async () => {
    mockApi.uploadFile.mockResolvedValue({ id: "f1", extraction_status: "completed" });
    const onSend = vi.fn();
    const m = mount(VISION.id, onSend);

    await act(async () => pasteImage(m.textarea, pngFile()));
    expect(useChatStore.getState().pendingFiles["c1"]).toHaveLength(1);

    m.switchModel(TEXT_ONLY.id);
    fireEvent.change(m.textarea, { target: { value: "describe this" } });
    fireEvent.keyDown(m.textarea, { key: "Enter" });

    expect(onSend).not.toHaveBeenCalled();
    // The attachment survives — nothing is silently dropped.
    expect(useChatStore.getState().pendingFiles["c1"]).toHaveLength(1);
    expect(useToastStore.getState().message).toContain("GPT Vision");
    expect(useToastStore.getState().action?.label).toContain("GPT Vision");

    // Switching back re-enables the send.
    m.switchModel(VISION.id);
    fireEvent.keyDown(m.textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("gateImagesForVision (the drag-drop pages' gate) keeps every file and offers the switch", () => {
    const pdf = new File(["%PDF"], "doc.pdf", { type: "application/pdf" });
    const png = pngFile();
    const kept = gateImagesForVision([png, pdf], TEXT_ONLY.id);

    expect(kept).toEqual([png, pdf]);
    expect(useToastStore.getState().message).toContain("GPT Vision");
    expect(useToastStore.getState().action?.label).toContain("GPT Vision");

    // Auto and unknown ids stay optimistic (desktop parity).
    expect(gateImagesForVision([pngFile()], "auto")).toHaveLength(1);
    expect(gateImagesForVision([pngFile()], "not-in-catalog")).toHaveLength(1);
  });
});
