// SSE parser unit tests — frame splitting, partial frames across
// chunks, the [DONE]-style non-JSON sentinel passthrough, and CRLF
// (\r\n\r\n) frame delimiters (E4 — valid per the SSE spec).

import { describe, expect, it } from "vitest";

import { parseSSE, type StreamEvent } from "./stream";

const enc = new TextEncoder();

/** Build a fake streaming Response from a list of raw byte chunks. */
function chunkedResponse(chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return { body: stream } as unknown as Response;
}

async function collect<T = unknown>(
  chunks: string[],
  signal?: AbortSignal,
): Promise<StreamEvent<T>[]> {
  const out: StreamEvent<T>[] = [];
  for await (const evt of parseSSE<T>(chunkedResponse(chunks), signal)) {
    out.push(evt);
  }
  return out;
}

describe("parseSSE — LF (\\n\\n) frames", () => {
  it("parses multiple complete frames in one chunk", async () => {
    const events = await collect([
      'event: message_start\ndata: {"message_id":"m1","conversation_id":"c1"}\n\n' +
        'event: content_delta\ndata: {"delta":"hello"}\n\n',
    ]);
    expect(events).toEqual([
      {
        event: "message_start",
        data: { message_id: "m1", conversation_id: "c1" },
      },
      { event: "content_delta", data: { delta: "hello" } },
    ]);
  });

  it("reassembles a frame split across chunks (mid-line, mid-JSON)", async () => {
    const events = await collect([
      "event: content_del",
      'ta\ndata: {"del',
      'ta":"partial ok"}\n',
      "\n",
    ]);
    expect(events).toEqual([
      { event: "content_delta", data: { delta: "partial ok" } },
    ]);
  });

  it("buffers a trailing partial frame until its blank-line delimiter arrives", async () => {
    const events = await collect([
      'event: content_delta\ndata: {"delta":"a"}\n\nevent: content_delta\ndata: {"delta":"b"}',
      '\n\nevent: message_complete\ndata: {"message_id":"m1"}\n\n',
    ]);
    expect(events.map((e) => e.event)).toEqual([
      "content_delta",
      "content_delta",
      "message_complete",
    ]);
    expect(events[1].data).toEqual({ delta: "b" });
  });

  it("yields a trailing frame that never got a final blank line", async () => {
    const events = await collect(['event: error\ndata: {"code":"boom","message":"x"}']);
    expect(events).toEqual([
      { event: "error", data: { code: "boom", message: "x" } },
    ]);
  });

  it("passes non-JSON data through as a raw string ([DONE] sentinel)", async () => {
    const events = await collect(["data: [DONE]\n\n"]);
    expect(events).toEqual([{ event: "message", data: "[DONE]" }]);
  });

  it("ignores frames with no data lines (comments / heartbeats)", async () => {
    const events = await collect([
      ': keep-alive\n\nevent: ping\n\ndata: {"delta":"real"}\n\n',
    ]);
    expect(events).toEqual([{ event: "message", data: { delta: "real" } }]);
  });

  it("returns nothing for a null body", async () => {
    const out: StreamEvent[] = [];
    for await (const evt of parseSSE({ body: null } as unknown as Response)) {
      out.push(evt);
    }
    expect(out).toEqual([]);
  });

  it("stops yielding once the abort signal fires", async () => {
    const ctrl = new AbortController();
    ctrl.abort();
    // Aborted before the first read → no events, no throw.
    const events = await collect(
      ['data: {"delta":"never"}\n\n'],
      ctrl.signal,
    );
    expect(events).toEqual([]);
  });
});

describe("parseSSE — CRLF (\\r\\n\\r\\n) frames (E4)", () => {
  it("parses CRLF-delimited frames", async () => {
    const events = await collect([
      'event: message_start\r\ndata: {"message_id":"m1","conversation_id":"c1"}\r\n\r\n' +
        'event: content_delta\r\ndata: {"delta":"crlf ok"}\r\n\r\n',
    ]);
    expect(events).toEqual([
      {
        event: "message_start",
        data: { message_id: "m1", conversation_id: "c1" },
      },
      { event: "content_delta", data: { delta: "crlf ok" } },
    ]);
  });

  it("reassembles a CRLF frame whose boundary is split across chunks", async () => {
    const events = await collect([
      'event: content_delta\r\ndata: {"delta":"split"}\r',
      "\n\r",
      '\nevent: message_complete\r\ndata: {"message_id":"m1"}\r\n\r\n',
    ]);
    expect(events).toEqual([
      { event: "content_delta", data: { delta: "split" } },
      { event: "message_complete", data: { message_id: "m1" } },
    ]);
  });

  it("handles mixed LF and CRLF frames in one stream", async () => {
    const events = await collect([
      'data: {"delta":"lf"}\n\ndata: {"delta":"crlf"}\r\n\r\n',
    ]);
    expect(events).toEqual([
      { event: "message", data: { delta: "lf" } },
      { event: "message", data: { delta: "crlf" } },
    ]);
  });
});

// ── Batch 2 T2 — heartbeat comment frames ──────────────────────────

describe("parseSSE — heartbeat comments", () => {
  it("never yields comment frames as events, but counts them as activity", async () => {
    const activity: number[] = [];
    const out: StreamEvent[] = [];
    const res = chunkedResponse([
      ": ping\n\n",
      'event: content_delta\ndata: {"delta":"x"}\n\n',
      ": ping\n\n",
    ]);
    for await (const evt of parseSSE(res, undefined, () => activity.push(1))) {
      out.push(evt);
    }
    // Comments are invisible to consumers…
    expect(out).toEqual([{ event: "content_delta", data: { delta: "x" } }]);
    // …but their BYTES reset the idle clock (one callback per chunk).
    expect(activity.length).toBe(3);
  });

  it("a comment interleaved inside an event frame does not corrupt it", async () => {
    const events = await collect([
      ": ping\n\nevent: message_complete\ndata: {\"message_id\":\"m1\"}\n\n: ping\n\n",
    ]);
    expect(events).toEqual([
      { event: "message_complete", data: { message_id: "m1" } },
    ]);
  });
});
