// A2 — tool_activity SSE handling. Pins: started adds a running row,
// finished collapses it in place (upsert by id), message_complete
// clears the ephemeral list, and multiple concurrent tools coexist.
// Mirrors the harness of chatStore.stream.test.ts.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
  },
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

vi.mock("@/hooks/useUsage", () => ({
  bumpUsageVersion: vi.fn(),
}));

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push(raw: string) {
      controller.enqueue(enc.encode(raw));
    },
    close() {
      controller.close();
    },
  };
}

const tick = () => new Promise<void>((r) => setTimeout(r, 0));

function conv(id: string): Conversation {
  return {
    id,
    title: "T",
    model_id: "claude-sonnet-4",
    message_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function assistant(convId: string): Message | undefined {
  const list = useChatStore.getState().messages[convId] ?? [];
  return list.find((m) => m.role === "assistant");
}

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => {});
  useChatStore.setState({
    conversations: [conv("c1")],
    messages: { c1: [] },
    messagesLoaded: { c1: true },
  });
  mockApi.getConversation.mockResolvedValue(conv("c1"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("tool_activity SSE", () => {
  it("started adds a running row; finished collapses it in place; complete clears", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    const sendP = useChatStore.getState().sendMessage("c1", "hi");
    await tick();

    s.push(frame("message_start", { message_id: "a1" }));
    s.push(
      frame("tool_activity", { id: "t1", tool: "save_memory", phase: "started" }),
    );
    await tick();
    expect(assistant("c1")?.tool_activities).toEqual([
      { id: "t1", tool: "save_memory", phase: "started" },
    ]);

    // A second tool starts while the first still runs.
    s.push(
      frame("tool_activity", {
        id: "t2",
        tool: "some_mcp_tool",
        phase: "started",
      }),
    );
    await tick();
    expect(assistant("c1")?.tool_activities).toHaveLength(2);

    // finished upserts by id — same row, not a new one.
    s.push(
      frame("tool_activity", { id: "t1", tool: "save_memory", phase: "finished" }),
    );
    await tick();
    const acts = assistant("c1")?.tool_activities ?? [];
    expect(acts).toHaveLength(2);
    expect(acts[0]).toEqual({ id: "t1", tool: "save_memory", phase: "finished" });
    expect(acts[1].phase).toBe("started");

    // message_complete clears the ephemeral list entirely.
    s.push(frame("message_complete", { message_id: "a1" }));
    s.close();
    await sendP;
    expect(assistant("c1")?.tool_activities).toBeUndefined();
  });
});
