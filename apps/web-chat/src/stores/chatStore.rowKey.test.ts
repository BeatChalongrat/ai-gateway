// FIX47.5 Part B — the row a message renders under never changes.
//
// An optimistic row is born with a temporary id; message_start /
// message_complete replace it with the persisted one. React keys on the
// render key (Message.client_key ?? id), so the swap must leave that key
// exactly where it was — otherwise the bubble unmounts and mounts again
// mid-send. Same rule for every swap path (user row, assistant row, the
// host's message_complete naming).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
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

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

function makeStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
  return {
    response: { ok: true, status: 200, body: stream } as unknown as Response,
    push: (raw: string) => controller.enqueue(enc.encode(raw)),
    close: () => controller.close(),
  };
}

const conv = (id: string): Conversation => ({
  id, title: "T", model_id: "claude-sonnet-4", message_count: 0,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
});

const rowKey = (m: { id: string; client_key?: string }) => m.client_key ?? m.id;

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.getConversation.mockRejectedValue(new Error("offline"));
  mockApi.generateTitle.mockResolvedValue({ title: "t" });
  useChatStore.setState({ conversations: [conv("c1")], messages: { c1: [] }, messagesLoaded: { c1: true } });
});
afterEach(() => vi.unstubAllGlobals());

describe("the render key survives the id swap", () => {
  it("message_start renames the user and assistant rows without changing their keys", async () => {
    const s = makeStream();
    mockApi.openMessageStream.mockResolvedValue(s.response);
    const done = useChatStore.getState().sendMessage("c1", "hello");
    await tick();
    const [u0, a0] = useChatStore.getState().messages.c1;
    expect(u0.id).toMatch(/^tmp-user-/);
    expect(a0.id).toMatch(/^tmp-asst-/);
    const userKey = rowKey(u0);
    const asstKey = rowKey(a0);

    s.push(frame("message_start", { message_id: "asst-real", user_message_id: "user-real", conversation_id: "c1" }));
    await tick();
    const [u1, a1] = useChatStore.getState().messages.c1;
    // the server's ids are on the rows…
    expect(u1.id).toBe("user-real");
    expect(a1.id).toBe("asst-real");
    // …and the keys the DOM renders under did not move
    expect(rowKey(u1)).toBe(userKey);
    expect(rowKey(a1)).toBe(asstKey);

    s.push(frame("message_complete", { message_id: "asst-real", conversation_id: "c1" }));
    s.close();
    await done;
    const [u2, a2] = useChatStore.getState().messages.c1;
    expect(rowKey(u2)).toBe(userKey);
    expect(rowKey(a2)).toBe(asstKey);
  });

  it("a row loaded from the server keeps id as its key (nothing to preserve)", () => {
    useChatStore.setState({
      messages: { c1: [{ id: "srv-1", role: "user", content: "x", status: "completed", created_at: "2026-01-01T00:00:00Z" }] },
    });
    expect(rowKey(useChatStore.getState().messages.c1[0])).toBe("srv-1");
  });
});
