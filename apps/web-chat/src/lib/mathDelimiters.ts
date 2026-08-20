// Batch 2 T1 — math delimiter normalization.
//
// GPT-family models emit LaTeX-style \[…\] (display) and \(…\) (inline)
// by default; remark-math only parses $-delimiters, so those formulas
// reached the user as literal backslash-bracket text. This normalizer
// converts the backslash forms to the $-forms so the existing
// remark-math → rehype-katex path renders them, WITHOUT touching
// anything that merely looks like math.
//
// Safety rules (each pinned by mathDelimiters.test.ts, whose corpus
// also runs against the desktop port — Rendering/MathDelimiters.cs —
// so the two clients agree byte-for-byte):
//   - fenced code and $$-math regions are protected via the SAME
//     line-state rules the block splitter uses (stepFenceState — one
//     fence grammar, one home);
//   - inline code spans (any backtick run length) are copied verbatim;
//     an UNCLOSED code span protects everything after it (streaming);
//   - an unterminated \[ or \( stays literal until its closer arrives —
//     mid-stream a formula reads as source text, then snaps to math on
//     close, the same behaviour $$ has today;
//   - \(…\) must close on the SAME line (inline math never spans lines
//     in practice; a line-spanning candidate is left alone);
//   - \]( is treated as an escaped-bracket markdown-link lookalike and
//     left alone;
//   - \\[ is a literal backslash followed by a bracket — escaped pairs
//     are consumed as a unit and never start a delimiter;
//   - $ and $$ forms are never touched (so "between $100 and $200"
//     stays exactly as risky as it already was, no more).

import { type FenceState, stepFenceState } from "./markdownBlocks";

export function normalizeMathDelimiters(src: string): string {
  // Fast path — the overwhelming majority of messages carry no
  // backslash delimiters at all.
  if (!src.includes("\\[") && !src.includes("\\(")) return src;

  const state: FenceState = { inFence: false, inMathBlock: false };
  const out: string[] = [];
  let run: string[] = [];

  const flushRun = () => {
    if (run.length === 0) return;
    out.push(convertRun(run.join("\n")));
    run = [];
  };

  for (const line of src.split("\n")) {
    const wasInRegion = state.inFence || state.inMathBlock;
    const isDelimiter = stepFenceState(state, line);
    const inRegion = state.inFence || state.inMathBlock;
    if (isDelimiter || wasInRegion || inRegion) {
      // Fence/math delimiter lines and everything inside a region pass
      // through untouched.
      flushRun();
      out.push(line);
    } else {
      run.push(line);
    }
  }
  flushRun();
  return out.join("\n");
}

/** Converts \[…\] / \(…\) inside one top-level text run (no fences /
 *  $$-regions — the caller stripped those). Inline-code aware. */
function convertRun(text: string): string {
  let out = "";
  let i = 0;
  while (i < text.length) {
    const ch = text[i];

    // Inline code span — copy verbatim through the matching close run
    // (same backtick count, CommonMark rule). An unclosed span copies
    // the remainder verbatim: while streaming, a half-typed code span
    // must protect whatever follows it.
    if (ch === "`") {
      let runLen = 0;
      while (text[i + runLen] === "`") runLen++;
      const close = findBacktickClose(text, i + runLen, runLen);
      if (close === -1) {
        out += text.slice(i);
        break;
      }
      out += text.slice(i, close + runLen);
      i = close + runLen;
      continue;
    }

    // Markdown link / image label — copy the whole construct verbatim.
    // Inside a label, `\[` and `\]` are markdown ESCAPES (a literal
    // bracket), not display-math delimiters, so the label is a skip region
    // exactly like an inline code span. An unclosed label falls through to
    // normal scanning, which keeps the streaming behaviour: a half-typed
    // construct converts nothing until it closes.
    if (ch === "[") {
      const past = linkConstructEnd(text, i);
      if (past !== -1) {
        out += text.slice(i, past);
        i = past;
        continue;
      }
    }

    if (ch === "\\") {
      const next = text[i + 1];
      // Escaped backslash — consume the pair so \\[ never reads as \[.
      if (next === "\\") {
        out += "\\\\";
        i += 2;
        continue;
      }
      if (next === "[") {
        const close = findCloser(text, i + 2, "]", false);
        // \]( is an escaped-bracket link lookalike, not display math.
        if (close !== -1 && text[close + 2] !== "(") {
          const inner = text.slice(i + 2, close);
          // A construct that occupies whole lines becomes a real $$
          // BLOCK (remark-math's math-flow — true display mode; the
          // desktop MathGuard treats it as a MathBlock). Mid-paragraph
          // occurrences become inline $$…$$, exactly what typing $$
          // inline produces today on both clients.
          if (isAtLineStart(text, i) && isAtLineEnd(text, close + 2)) {
            out += "$$\n" + trimNewlines(inner) + "\n$$";
          } else {
            out += "$$" + inner + "$$";
          }
          i = close + 2;
          continue;
        }
      }
      if (next === "(") {
        const close = findCloser(text, i + 2, ")", true);
        if (close !== -1) {
          out += "$" + text.slice(i + 2, close) + "$";
          i = close + 2;
          continue;
        }
      }
    }

    out += ch;
    i++;
  }
  return out;
}

/**
 * If `text[start]` opens a markdown link or image label that is actually
 * part of a link — `[label](url)` or `[label][ref]` — returns the index just
 * past the whole construct. Returns -1 for a bare `[…]` that is not a link
 * (those keep converting as before) and for an unclosed label.
 *
 * Bracket depth ignores escaped brackets, so `[a \[b\] c](url)` closes on
 * the real `]`, not the escaped one.
 */
function linkConstructEnd(text: string, start: number): number {
  let depth = 1;
  let j = start + 1;
  let labelEnd = -1;
  while (j < text.length) {
    const c = text[j];
    if (c === "\\") {
      j += 2; // escaped char (incl. \[ and \]) — never affects depth
      continue;
    }
    if (c === "\n") break; // a label spanning lines is not worth guessing at
    if (c === "[") depth++;
    else if (c === "]") {
      depth--;
      if (depth === 0) {
        labelEnd = j;
        break;
      }
    }
    j++;
  }
  if (labelEnd === -1) return -1;

  const after = text[labelEnd + 1];
  const closer = after === "(" ? ")" : after === "[" ? "]" : null;
  if (closer === null) return -1; // `[text]` alone — not a link

  for (let k = labelEnd + 2; k < text.length; k++) {
    const c = text[k];
    if (c === "\\") {
      k++;
      continue;
    }
    if (c === "\n") return -1; // unterminated target
    if (c === closer) return k + 1;
  }
  return -1;
}

/** Only whitespace between the previous newline (or start) and `pos`. */
function isAtLineStart(text: string, pos: number): boolean {
  for (let j = pos - 1; j >= 0; j--) {
    const c = text[j];
    if (c === "\n") return true;
    if (c !== " " && c !== "\t") return false;
  }
  return true;
}

/** Only whitespace between `pos` and the next newline (or end). */
function isAtLineEnd(text: string, pos: number): boolean {
  for (let j = pos; j < text.length; j++) {
    const c = text[j];
    if (c === "\n") return true;
    if (c !== " " && c !== "\t") return false;
  }
  return true;
}

function trimNewlines(s: string): string {
  return s.replace(/^[\s\n]+/, "").replace(/[\s\n]+$/, "");
}

/** Index of the `\` of the first unescaped `\x` closer at or after
 *  `from`, or -1. `sameLine` stops the search at the first newline
 *  (inline math must close on its own line). */
function findCloser(
  text: string,
  from: number,
  closerChar: string,
  sameLine: boolean,
): number {
  for (let j = from; j < text.length - 1; j++) {
    const c = text[j];
    if (sameLine && c === "\n") return -1;
    if (c === "\\") {
      if (text[j + 1] === "\\") {
        j++; // escaped pair — skip both
        continue;
      }
      if (text[j + 1] === closerChar) return j;
    }
  }
  return -1;
}

/** Index of the next backtick run of EXACTLY `runLen` ticks, or -1. */
function findBacktickClose(text: string, from: number, runLen: number): number {
  let j = from;
  while (j < text.length) {
    if (text[j] === "`") {
      let l = 0;
      while (text[j + l] === "`") l++;
      if (l === runLen) return j;
      j += l;
    } else {
      j++;
    }
  }
  return -1;
}
