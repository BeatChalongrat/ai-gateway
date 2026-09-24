// FIX132 B4 — the knowledge-base sources of an answer arrive with the answer,
// not only after a reload. chat-edge stores them on the assistant row
// (context_sources: knowledge_chunk / knowledge_pending) and sends no stream
// frame for them, so before this a live answer's [Source: …] pills had no
// passages and "แหล่งข้อมูล" was missing until the page was reloaded. When the
// turn ends in a chat that searches a knowledge base (its own, or its
// project's), the store reads the finished row once and copies its sources
// onto the answer. A chat without a knowledge base makes no extra request.
// Same mocks and hand-fed SSE stream as chatStore.memoryMarkers.test.ts.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ContextSource, Conversation, Message, Project } from "@/lib/types";

const { mockApi, mockBumpUsage, mockPendingRefresh } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    openResumeStream: vi.fn(),
    listMessages: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
    cancelGeneration: vi.fn(),
    renameConversation: vi.fn(),
  },
  mockBumpUsage: vi.fn(),
  mockPendingRefresh: vi.fn(async () => {}),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public code: string,
      message: string,
      public status: number,
    ) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: mockBumpUsage }));
vi.mock("@/hooks/useMemoryPendingCount", () => ({
  useMemoryPendingStore: { getState: () => ({ refresh: mockPendingRefresh }) },
}));

import * as chatStoreModule from "./chatStore";
import { RESUME_RETRY_DELAYS, RESUME_WAIT, useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const originalDelays = [...RESUME_RETRY_DELAYS];
const retry = (chatStoreModule as { KNOWLEDGE_SOURCES_RETRY?: { delayMs: number } }).KNOWLEDGE_SOURCES_RETRY;
const originalRetry = retry?.delayMs;
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push: (raw: string) => controller.enqueue(enc.encode(raw)),
    close: () => controller.close(),
  };
}
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
const conv = (over: Partial<Conversation> = {}): Conversation =>
  ({ id: "c1", title: "T", model_id: "m", message_count: 0, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", ...over }) as Conversation;
const messagesOf = (id: string): Message[] => useChatStore.getState().messages[id] ?? [];
const KB_SOURCES: ContextSource[] = [
  {
    id: "s1", source_type: "knowledge_chunk", source_id: "chunk-1", display_label: "คู่มือพนักงาน.pdf",
    metadata: { kb_id: "kb1", document_id: "d1", filename: "คู่มือพนักงาน.pdf", chunk_index: 2, excerpt: "ลาพักร้อนได้ 10 วัน", score: 0.9, rank: 1 },
    created_at: "2026-09-24T00:00:00Z",
  } as unknown as ContextSource,
];
const serverRow = (sources?: ContextSource[]): Message =>
  ({ id: "real-1", role: "assistant", content: "ลาพักร้อนได้ 10 วัน [Source: คู่มือพนักงาน.pdf]", status: "completed", created_at: "", context_sources: sources }) as Message;

async function runTurn() {
  const s = makeStream();
  mockApi.openMessageStream.mockResolvedValue(s.response);
  const done = useChatStore.getState().sendMessage("c1", "ลาพักร้อนได้กี่วัน");
  await tick();
  s.push(frame("message_start", { message_id: "real-1", conversation_id: "c1" }));
  s.push(frame("content_delta", { delta: "ลาพักร้อนได้ 10 วัน [Source: คู่มือพนักงาน.pdf]" }));
  s.push(frame("message_complete", { message_id: "real-1" }));
  s.close();
  await done;
}

beforeEach(() => {
  vi.clearAllMocks();
  RESUME_WAIT.capMs = 0;
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays.map(() => 0));
  if (retry) retry.delayMs = 10;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  mockApi.getConversation.mockResolvedValue(conv());
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
  mockApi.cancelGeneration.mockResolvedValue({ cancelled: true });
});
afterEach(() => {
  RESUME_RETRY_DELAYS.splice(0, RESUME_RETRY_DELAYS.length, ...originalDelays);
  if (retry && originalRetry !== undefined) retry.delayMs = originalRetry;
  vi.unstubAllGlobals();
});

describe("FIX132 — a live answer gets its knowledge-base sources", () => {
  it("a chat with its own knowledge base: the finished row's sources land on the answer", async () => {
    useChatStore.setState({ ...initialState, conversations: [conv({ knowledge_base_id: "kb1" })], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
    mockApi.listMessages.mockResolvedValue({ messages: [serverRow(KB_SOURCES)], has_more: false });
    await runTurn();
    await vi.waitFor(() => expect(messagesOf("c1")[1].context_sources).toEqual(KB_SOURCES));
    expect(mockApi.listMessages).toHaveBeenCalledWith("c1", expect.objectContaining({ limit: expect.any(Number) }));
    // The streamed text is untouched.
    expect(messagesOf("c1")[1].content).toBe("ลาพักร้อนได้ 10 วัน [Source: คู่มือพนักงาน.pdf]");
  });

  it("a chat inside a project with a knowledge base does the same", async () => {
    const project = { id: "p1", name: "HR", instructions: "", knowledge_base_id: "kb1", conversation_count: 1, created_at: "", updated_at: "" } as Project;
    useChatStore.setState({ ...initialState, conversations: [conv({ project_id: "p1" })], projects: [project], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
    mockApi.listMessages.mockResolvedValue({ messages: [serverRow(KB_SOURCES)], has_more: false });
    await runTurn();
    await vi.waitFor(() => expect(messagesOf("c1")[1].context_sources).toEqual(KB_SOURCES));
  });

  it("sources saved a moment after the answer are picked up by one retry", async () => {
    useChatStore.setState({ ...initialState, conversations: [conv({ knowledge_base_id: "kb1" })], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
    mockApi.listMessages
      .mockResolvedValueOnce({ messages: [serverRow(undefined)], has_more: false })
      .mockResolvedValue({ messages: [serverRow(KB_SOURCES)], has_more: false });
    await runTurn();
    await vi.waitFor(() => expect(messagesOf("c1")[1].context_sources).toEqual(KB_SOURCES));
    expect(mockApi.listMessages).toHaveBeenCalledTimes(2);
  });

  it("a chat without a knowledge base makes no extra request", async () => {
    useChatStore.setState({ ...initialState, conversations: [conv()], messages: { c1: [] }, messagesLoaded: { c1: true } }, true);
    await runTurn();
    await new Promise((r) => setTimeout(r, 30));
    expect(mockApi.listMessages).not.toHaveBeenCalled();
    expect(messagesOf("c1")[1].context_sources).toBeUndefined();
  });
});
