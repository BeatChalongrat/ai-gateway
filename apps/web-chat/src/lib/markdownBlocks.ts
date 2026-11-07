/**
 * Fence-aware top-level markdown block splitter.
 *
 * Ports the desktop BlockDiff idea (SranDesktop Rendering/BlockDiff.cs) to
 * the web streaming path: split a markdown string on blank lines into
 * top-level blocks so a memoized renderer only re-parses the block that is
 * still growing. A naive split on "\n\n" would cut code fences and math
 * blocks apart, so blank lines only count as boundaries outside fences.
 *
 * Contract that makes the memoization work: appending text to `src` may
 * only ever change the LAST block — every earlier block must come back
 * byte-identical (see markdownBlocks.test.ts "stability").
 */

export interface MarkdownBlock {
  key: string;
  text: string;
}

/** Line-level fence/math-region state — the ONE home for the "is this
 *  line inside a protected region" rules. splitMarkdownBlocks and the
 *  math-delimiter normalizer (lib/mathDelimiters.ts) both consume it so
 *  the two can never disagree about where a fence starts or ends. The
 *  desktop port (Rendering/MathDelimiters.cs) mirrors these exact rules. */
export interface FenceState {
  inFence: boolean;
  inMathBlock: boolean;
}

/** Applies one line to the state. Returns true when the line itself is a
 *  region delimiter (``` / ~~~ / $$) — a delimiter line belongs to the
 *  region it opens or closes. A $$ line inside a fence is code, and a
 *  fence marker inside a math block is math — each state only toggles
 *  while the other is off. */
export function stepFenceState(state: FenceState, line: string): boolean {
  const trimmed = line.trim();
  if (!state.inMathBlock && (trimmed.startsWith("```") || trimmed.startsWith("~~~"))) {
    state.inFence = !state.inFence;
    return true;
  }
  if (!state.inFence && trimmed.startsWith("$$")) {
    state.inMathBlock = !state.inMathBlock;
    return true;
  }
  return false;
}

export function splitMarkdownBlocks(src: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const current: string[] = [];
  const state: FenceState = { inFence: false, inMathBlock: false };

  const flush = () => {
    if (current.length === 0) return;
    const text = current.join("\n");
    current.length = 0;
    // Skip blocks that are only whitespace (e.g. leading blank lines).
    if (text.trim().length === 0) return;
    blocks.push({ key: String(blocks.length), text });
  };

  for (const line of src.split("\n")) {
    if (stepFenceState(state, line)) {
      current.push(line);
      continue;
    }

    // Blank line = block boundary, but only at the top level. Inside a
    // fence or math block it is content. Consecutive blank lines collapse
    // into a single boundary because flush() on an empty buffer is a no-op.
    if (line.trim() === "" && !state.inFence && !state.inMathBlock) {
      flush();
      continue;
    }

    current.push(line);
  }

  // Tail block — covers the still-streaming case, including an unclosed
  // fence: everything from the opening fence line onward stays one block.
  flush();

  return blocks;
}
