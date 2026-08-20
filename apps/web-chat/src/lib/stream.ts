// SSE parser — converts a fetch ReadableStream Response body into
// an async iterable of parsed events.
//
// Wire format from chat-edge-api:
//
//   event: message_start
//   data: {"message_id":"…","conversation_id":"…"}
//
//   event: content_delta
//   data: {"delta":"some text"}
//
//   event: message_complete
//   data: {"message_id":"…","token_usage":{"input":N,"output":N}}
//
//   event: error
//   data: {"code":"…","message":"…"}
//
// Each frame is delimited by a blank line; ``data:`` payloads are
// JSON. Multi-line ``data:`` is concatenated per the SSE spec but
// the server never emits that shape, so we keep the parser linear.

export interface StreamEvent<T = unknown> {
  event: string;
  data: T;
}

// Blank line separating SSE frames — LF or CRLF line endings (the
// spec allows both; chat-edge emits LF but a proxy may normalise to
// CRLF). Not global/sticky: we always re-match from the buffer head.
const FRAME_BOUNDARY = /\r?\n\r?\n/;

export async function* parseSSE<T = unknown>(
  response: Response,
  signal?: AbortSignal,
  // T2 — liveness callback, fired on every chunk of BYTES received.
  // Heartbeat comment frames (": ping") are deliberately never yielded
  // as events (no data: line), so an idle watchdog that only counted
  // yielded events would false-positive during a long quiet tool run;
  // counting bytes makes heartbeats reset the clock.
  onActivity?: () => void,
): AsyncGenerator<StreamEvent<T>> {
  if (!response.body) {
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) {
        await reader.cancel();
        return;
      }
      const { done, value } = await reader.read();
      if (done) break;
      onActivity?.();
      buffer += decoder.decode(value, { stream: true });

      // Split on blank-line frames. Keep the trailing partial frame
      // in buffer so a flush mid-line doesn't lose data. Per the SSE
      // spec a frame boundary is a blank line with either LF or CRLF
      // line endings — accept \n\n, \r\n\r\n, and the mixed forms.
      let boundary = buffer.match(FRAME_BOUNDARY);
      while (boundary && boundary.index !== undefined) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const evt = parseFrame<T>(frame);
        if (evt) yield evt;
        boundary = buffer.match(FRAME_BOUNDARY);
      }
    }
    // Handle any trailing frame without a final blank line.
    if (buffer.trim().length) {
      const evt = parseFrame<T>(buffer);
      if (evt) yield evt;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* already released */
    }
  }
}

function parseFrame<T>(raw: string): StreamEvent<T> | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  const dataStr = dataLines.join("\n");
  try {
    return { event: eventName, data: JSON.parse(dataStr) as T };
  } catch {
    return { event: eventName, data: dataStr as unknown as T };
  }
}
