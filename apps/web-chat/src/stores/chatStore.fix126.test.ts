// FIX126 F1 — the server refuses a message with more than 5 files: 422
// TOO_MANY_FILES before the stream opens, or an error frame with that code.
// Same shape as CONVERSATION_BUSY: nothing was sent, so this send's user and
// assistant rows go (no error bubble, no duplicate question), the typed text
// is back in the composer, and a localized notice says what to do.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    listMessages: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
    cancelGeneration: vi.fn(),
    renameConversation: vi.fn(),
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
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import { clearDraftStore, loadDraft } from "@/lib/draftStore";
import { tOutside, useI18nStore } from "@/lib/i18n";
import { clearOutbox, outboxPeek } from "@/lib/sendOutbox";

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const RUN = "5f3c1a2b-0000-4000-8000-0123456789ab";

function conv(id: string): Conversation {
  return { id, title: "t", model_id: "qwen3.8-27b-fp8", message_count: 2, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
}
const earlier: Message[] = [
  { id: "u0", role: "user", content: "ก่อนหน้า", status: "completed", created_at: "2026-01-01T00:00:00Z" } as Message,
  { id: "a0", role: "assistant", content: "ตอบแล้ว", status: "completed", created_at: "2026-01-01T00:00:01Z" } as Message,
];
const refused = (status: number, code: string, message: string) =>
  ({ ok: false, status, statusText: "Unprocessable Entity", text: async () => JSON.stringify({ error: { code, message } }) }) as unknown as Response;

function frameStream(frames: string) {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(enc.encode(frames));
      c.close();
    },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}
const frame = (event: string, data: Record<string, unknown>) => `event: ${event}\ndata: ${JSON.stringify({ run_id: RUN, ...data })}\n\n`;

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  clearDraftStore();
  clearOutbox();
  useI18nStore.setState({ locale: "th" });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.listMessages.mockResolvedValue({ messages: [...earlier] });
  useChatStore.setState({ conversations: [conv("c1")], messages: { c1: [...earlier] }, messagesLoaded: { c1: true } });
});
afterEach(() => vi.unstubAllGlobals());

describe("FIX126 F1 — 422 TOO_MANY_FILES", () => {
  it("removes this send's rows, keeps the text, and shows the localized notice with the count", async () => {
    mockApi.openMessageStream.mockResolvedValueOnce(
      refused(422, "TOO_MANY_FILES", "at most 5 files per message (this message has 7)"),
    );
    await useChatStore.getState().sendMessage("c1", "สรุปไฟล์ทั้งหมด", ["f1", "f2", "f3", "f4", "f5", "f6", "f7"]);

    const s = useChatStore.getState();
    expect(s.messages.c1.map((m) => m.id)).toEqual(["u0", "a0"]);
    expect(s.messages.c1.some((m) => m.status === "error")).toBe(false);
    expect(s.returnedDrafts.c1?.text).toBe("สรุปไฟล์ทั้งหมด");
    expect(loadDraft("c1")).toBe("สรุปไฟล์ทั้งหมด");
    expect(outboxPeek("c1")).toBeNull();
    expect(s.errorBanner).toBe(tOutside("error.tooManyFiles", { max: "5", n: "7" }));
    expect(s.errorBanner).toContain("ส่งได้สูงสุด 5 ไฟล์ต่อข้อความ");
    expect(s.streamingAssistantId.c1).toBeUndefined();
  });

  it("the same refusal as an error frame: no leftover rows, text back, notice", async () => {
    mockApi.openMessageStream.mockResolvedValueOnce(
      frameStream(
        frame("message_start", { message_id: "a1", conversation_id: "c1", user_message_id: "u1" }) +
          frame("error", { code: "TOO_MANY_FILES", message: "at most 5 files per message (this message has 6)", retryable: false }),
      ),
    );
    await useChatStore.getState().sendMessage("c1", "อ่านหกไฟล์นี้", ["f1", "f2", "f3", "f4", "f5", "f6"]);

    const s = useChatStore.getState();
    expect(s.messages.c1.map((m) => m.id)).toEqual(["u0", "a0"]);
    expect(s.returnedDrafts.c1?.text).toBe("อ่านหกไฟล์นี้");
    expect(loadDraft("c1")).toBe("อ่านหกไฟล์นี้");
    expect(s.errorBanner).toBe(tOutside("error.tooManyFiles", { max: "5", n: "6" }));
    expect(s.streamingAssistantId.c1).toBeUndefined();
  });

  it("English notice without a count when the server sentence has none", async () => {
    useI18nStore.setState({ locale: "en" });
    mockApi.openMessageStream.mockResolvedValueOnce(refused(422, "TOO_MANY_FILES", "too many files"));
    await useChatStore.getState().sendMessage("c1", "read these", ["f1"]);
    expect(useChatStore.getState().errorBanner).toBe("At most 5 files per message — remove some and send again");
  });
});
