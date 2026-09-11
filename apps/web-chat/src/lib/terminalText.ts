// FIX107 Part A1 — text pasted from a terminal.
//
// A table a terminal draws with box characters (┌──┬──┐) has border lines with
// nowhere to break. In a user bubble those lines set the bubble's width: a psql
// table pasted into the web page measured 1,637 px inside a 1,280 px window,
// mostly empty, running off the left edge. MessageBubble now caps the bubble at
// its column, and this splits consecutive lines drawn with box characters into
// a block the bubble shows monospaced, unwrapped, scrolling sideways, so the
// table stays a table. Every other line renders as it always did; Thai prose
// never reaches the terminal path.

export type UserTextSegment = { kind: "text" | "terminal"; text: string };

// The Unicode box-drawing block, U+2500 to U+257F.
const BOX_DRAWING = /[─-╿]/;

/** A single decorated line is not a table; two consecutive ones are. */
const MIN_TERMINAL_LINES = 2;

export function splitTerminalBlocks(content: string): UserTextSegment[] {
  if (!BOX_DRAWING.test(content)) return [{ kind: "text", text: content }];
  const lines = content.split(/\r?\n/);
  const segments: UserTextSegment[] = [];
  let text: string[] = [];
  const flush = () => {
    if (text.length > 0) segments.push({ kind: "text", text: text.join("\n") });
    text = [];
  };
  let i = 0;
  while (i < lines.length) {
    let end = i;
    while (end < lines.length && BOX_DRAWING.test(lines[end]!)) end++;
    if (end - i >= MIN_TERMINAL_LINES) {
      flush();
      segments.push({ kind: "terminal", text: lines.slice(i, end).join("\n") });
      i = end;
    } else {
      text.push(lines[i]!);
      i++;
    }
  }
  flush();
  if (!segments.some((s) => s.kind === "terminal")) return [{ kind: "text", text: content }];
  return segments;
}
