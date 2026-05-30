"use client";

// Content renderer used WHILE an assistant message is still streaming.
//
// We now render the SAME full markdown pipeline as the completed
// message (ChatMarkdown), so tables, lists, headings, dividers, bold,
// task-lists and code highlighting all appear LIVE as tokens arrive —
// matching ChatGPT/Claude — instead of showing raw markdown until the
// turn completes and snapping into shape.
//
// Performance: re-parsing the whole buffer on every RAF-batched delta
// would be O(n) per frame → O(n²) over a long response. The store
// already RAF-batches deltas; on top of that we THROTTLE the heavy
// re-parse to at most once per RENDER_THROTTLE_MS (trailing-edge
// scheduled), so even a multi-thousand-token answer re-parses ~12×/s
// rather than 60×/s. The final, full content is rendered by the
// completed pipeline the instant streaming ends (parent swaps us out),
// so the throttle never drops the last tokens.
//
// Plan cards (:::excel-plan etc.) are intentionally NOT extracted here
// — they mount only once the turn completes, so during streaming the
// raw fence shows as text (unchanged from before).

import { memo, useEffect, useRef, useState } from "react";

import { ChatMarkdown } from "./ChatMarkdown";

interface Props {
  content: string;
}

const RENDER_THROTTLE_MS = 80;

function StreamingContentImpl({ content }: Props) {
  // ``shown`` is the throttled snapshot of ``content`` we actually
  // hand to the markdown parser.
  const [shown, setShown] = useState(content);
  const lastRenderRef = useRef(0);
  const timerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    const now =
      typeof performance !== "undefined" ? performance.now() : Date.now();
    const elapsed = now - lastRenderRef.current;
    if (elapsed >= RENDER_THROTTLE_MS) {
      lastRenderRef.current = now;
      setShown(content);
    } else {
      // Trailing edge — schedule the update for the remainder of the
      // window so the very latest buffer still lands ~80ms later.
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        lastRenderRef.current =
          typeof performance !== "undefined" ? performance.now() : Date.now();
        setShown(content);
      }, RENDER_THROTTLE_MS - elapsed);
    }
    return () => {
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    };
  }, [content]);

  if (!content) {
    return null;
  }

  return (
    <div className="streaming-body">
      <ChatMarkdown text={shown} streaming />
      <span className="streaming-cursor-block" aria-hidden />
    </div>
  );
}

// Re-render only when the content actually changed — saves a pass when
// an unrelated store field flips.
export const StreamingContent = memo(StreamingContentImpl, (prev, next) => {
  return prev.content === next.content;
});
