"use client";

// Lightweight content renderer used WHILE an assistant message is
// still streaming. The full react-markdown pipeline re-parses the
// entire string on every paint, which becomes O(n) per token for
// long responses. During streaming we instead split on triple-
// backtick fences so code blocks render in a styled <pre> while the
// prose body renders as plain text with line breaks preserved.
//
// Once streaming completes the parent component swaps to the full
// react-markdown renderer (with GFM tables, links, etc.).

import { memo } from "react";

interface Props {
  content: string;
}

interface Segment {
  kind: "text" | "code";
  language?: string;
  body: string;
}

// Parse the streaming buffer into alternating text + code segments.
// Closes an open code block defensively when no terminating fence
// has arrived yet so the user sees the in-flight code in monospace.
function tokenize(content: string): Segment[] {
  const segs: Segment[] = [];
  let i = 0;
  while (i < content.length) {
    const fenceStart = content.indexOf("```", i);
    if (fenceStart === -1) {
      segs.push({ kind: "text", body: content.slice(i) });
      break;
    }
    if (fenceStart > i) {
      segs.push({ kind: "text", body: content.slice(i, fenceStart) });
    }
    // Read optional language tag up to the newline after the fence.
    const afterFence = fenceStart + 3;
    const nl = content.indexOf("\n", afterFence);
    let language = "";
    let bodyStart = afterFence;
    if (nl !== -1) {
      language = content.slice(afterFence, nl).trim();
      bodyStart = nl + 1;
    }
    const fenceEnd = content.indexOf("```", bodyStart);
    if (fenceEnd === -1) {
      // Unterminated — code is still streaming.
      segs.push({
        kind: "code",
        language,
        body: content.slice(bodyStart),
      });
      i = content.length;
      break;
    }
    segs.push({
      kind: "code",
      language,
      body: content.slice(bodyStart, fenceEnd),
    });
    i = fenceEnd + 3;
  }
  return segs;
}

function StreamingContentImpl({ content }: Props) {
  if (!content) {
    return null;
  }
  const segments = tokenize(content);
  return (
    <div className="streaming-body">
      {segments.map((seg, i) =>
        seg.kind === "code" ? (
          <pre
            key={i}
            className="code-block-inline"
            data-lang={seg.language || undefined}
          >
            <code>{seg.body}</code>
          </pre>
        ) : (
          <span
            key={i}
            className="streaming-text"
            // ``white-space: pre-wrap`` preserves user newlines + any
            // intermediate whitespace the LLM emits between tokens.
            style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
          >
            {seg.body}
          </span>
        ),
      )}
      <span className="streaming-cursor-block" aria-hidden />
    </div>
  );
}

// Re-render only when the content actually changed. The wrapper
// component still runs on every store update, but this saves the
// tokenize() pass on identical re-renders (e.g. when a sibling
// state field flips).
export const StreamingContent = memo(StreamingContentImpl, (prev, next) => {
  return prev.content === next.content;
});
