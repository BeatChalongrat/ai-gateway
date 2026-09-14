// FIX117 — the server's compaction progress reaches the desktop.
//   client:     POST /compact with Accept: text/event-stream — every
//               compaction_progress frame is handed to the listener in order,
//               the compact_result frame is the JSON the one-shot call gets,
//               an error frame yields null, and a server that answers with
//               plain JSON (pre-FIX117) is read as before.
//   summariser: forwards the reports, survives a listener that throws, and
//               keeps the one-JSON call when there is no listener or the
//               client cannot stream.
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatEdgeClient, type CompactionProgressDto } from "../src/main/chat-edge-client";
import { ChatEdgeSummariser } from "../src/main/server-summariser";
import type { Logger } from "../src/main/logger";

const quiet: Logger = { info: () => {}, warn: () => {}, error: () => {}, debug: () => {} } as unknown as Logger;

function sse(frames: [string, unknown][]): Response {
  const body = frames.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
  const enc = new TextEncoder();
  const chunks = [body.slice(0, 40), body.slice(40)]; // split mid-frame on purpose
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("FIX117 — ChatEdgeClient.compactConversationStream", () => {
  it("asks for text/event-stream, hands every report over in order, and returns the result frame", async () => {
    const result = { state: { current_goal: "g" }, boundary_message_id: "m9", messages_total: 36, coverage_reached: true };
    const fetchMock = vi.fn(async () => sse([
      ["compaction_progress", { folded: 0, total: 36, done: false, reached: false }],
      ["compaction_progress", { folded: 20, total: 36, done: false, reached: false }],
      ["compaction_progress", { folded: 36, total: 36, done: true, reached: true }],
      ["compact_result", result],
    ]));
    vi.stubGlobal("fetch", fetchMock);
    const c = new ChatEdgeClient("http://edge", () => "tok", quiet);
    const seen: CompactionProgressDto[] = [];
    const res = await c.compactConversationStream("c1", "keep numbers", "auto", (p) => seen.push(p));
    expect(res).toEqual(result);
    expect(seen).toEqual([
      { folded: 0, total: 36, done: false, reached: false },
      { folded: 20, total: 36, done: false, reached: false },
      { folded: 36, total: 36, done: true, reached: true },
    ]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://edge/api/v1/conversations/c1/compact");
    expect((init.headers as Record<string, string>).Accept).toBe("text/event-stream");
    expect(JSON.parse(init.body as string)).toEqual({ instructions: "keep numbers", trigger: "auto" });
  });

  it("an unknown total arrives as null — never a number the client made up", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sse([["compaction_progress", { folded: 3, total: null, done: false, reached: false }], ["compact_result", { state: {} }]])));
    const seen: CompactionProgressDto[] = [];
    await new ChatEdgeClient("http://edge", () => "tok", quiet).compactConversationStream("c1", "", "auto", (p) => seen.push(p));
    expect(seen).toEqual([{ folded: 3, total: null, done: false, reached: false }]);
  });

  it("an error frame ends in null, the same as the one-shot call's HTTP error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sse([["compaction_progress", { folded: 0, total: 3, done: false, reached: false }], ["error", { code: "bad_request", message: "nothing to compact yet" }]])));
    const res = await new ChatEdgeClient("http://edge", () => "tok", quiet).compactConversationStream("c1", "", "manual", () => {});
    expect(res).toBeNull();
  });

  it("a server that answers with plain JSON (pre-FIX117) is read as before", async () => {
    const result = { state: { current_goal: "g" }, coverage_reached: true };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(result), { status: 200, headers: { "Content-Type": "application/json" } })));
    const seen: CompactionProgressDto[] = [];
    const res = await new ChatEdgeClient("http://edge", () => "tok", quiet).compactConversationStream("c1", "", "auto", (p) => seen.push(p));
    expect(res).toEqual(result);
    expect(seen).toEqual([]);
  });
});

type Row = { id: string; role: string; content: string | null; status: string };
const ROWS: Row[] = [
  { id: "m1", role: "user", content: "q1", status: "completed" },
  { id: "m2", role: "assistant", content: "a1", status: "completed" },
];
const STATE = { current_goal: "g", summarized_up_to_message_id: "m2" };
function client(opts: { stream?: boolean; throwInListener?: boolean }) {
  const calls: string[] = [];
  const base = {
    calls,
    async compactConversation() {
      calls.push("json");
      return { state: STATE, boundary_message_id: "m2", coverage_reached: true };
    },
    async listMessagesPage() {
      return { messages: ROWS, hasMore: false };
    },
  };
  if (!opts.stream) return base;
  return {
    ...base,
    async compactConversationStream(_id: string, _i: string, _t: string, onProgress: (p: CompactionProgressDto) => void) {
      calls.push("stream");
      onProgress({ folded: 0, total: 2, done: false, reached: false });
      onProgress({ folded: 2, total: 2, done: true, reached: true });
      return { state: STATE, boundary_message_id: "m2", coverage_reached: true };
    },
  };
}

describe("FIX117 — ChatEdgeSummariser forwards progress", () => {
  const sig = new AbortController().signal;

  it("with a listener and a streaming client: the reports arrive in order and the result is the same", async () => {
    const c = client({ stream: true });
    const seen: CompactionProgressDto[] = [];
    const res = await new ChatEdgeSummariser(c as never, quiet).compact("c", "", "auto", sig, (p) => seen.push(p));
    expect(c.calls).toEqual(["stream"]);
    expect(seen.map((p) => `${p.folded}/${p.total}`)).toEqual(["0/2", "2/2"]);
    expect(res?.boundaryMessageId).toBe("m2");
    expect(res?.rowIds).toEqual(["m1", "m2"]);
  });

  it("a listener that throws never breaks the compaction", async () => {
    const c = client({ stream: true });
    const res = await new ChatEdgeSummariser(c as never, quiet).compact("c", "", "auto", sig, () => {
      throw new Error("page gone");
    });
    expect(res?.coverageReached).toBe(true);
  });

  it("no listener, or a client that cannot stream: the one-JSON call exactly as before", async () => {
    const c1 = client({ stream: true });
    await new ChatEdgeSummariser(c1 as never, quiet).compact("c", "", "auto", sig);
    expect(c1.calls).toEqual(["json"]);
    const c2 = client({ stream: false });
    const seen: CompactionProgressDto[] = [];
    const res = await new ChatEdgeSummariser(c2 as never, quiet).compact("c", "", "auto", sig, (p) => seen.push(p));
    expect(c2.calls).toEqual(["json"]);
    expect(seen).toEqual([]);
    expect(res?.coverageReached).toBe(true);
  });
});
