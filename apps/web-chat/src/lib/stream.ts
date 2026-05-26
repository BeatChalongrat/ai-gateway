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

export async function* parseSSE<T = unknown>(
  response: Response,
  signal?: AbortSignal,
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
      buffer += decoder.decode(value, { stream: true });

      // Split on blank-line frames. Keep the trailing partial frame
      // in buffer so a flush mid-line doesn't lose data.
      let frameEnd = buffer.indexOf("\n\n");
      while (frameEnd !== -1) {
        const frame = buffer.slice(0, frameEnd);
        buffer = buffer.slice(frameEnd + 2);
        const evt = parseFrame<T>(frame);
        if (evt) yield evt;
        frameEnd = buffer.indexOf("\n\n");
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
  for (const line of raw.split("\n")) {
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
