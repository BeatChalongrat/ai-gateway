"use client";

// Elevated code-block panel rendered by react-markdown's `pre`
// override. Styled like Claude / ChatGPT artefacts: distinct header
// strip with language (or filename) + copy button, code surface on
// its own background, subtle shadow + hover border so the panel
// reads as a card breaking out of the prose. Long blocks
// (> COLLAPSE_THRESHOLD_LINES) start collapsed; the user expands
// with a "Show more" toggle pinned to the bottom.
//
// Filename syntax: the language tag may carry an optional filename
// after a colon — e.g. ```javascript:app.js → "app.js" in the
// header instead of "JavaScript". This is a Claude convention the
// LLM picks up from prompt context and we surface it verbatim.

import { Children, isValidElement, useState } from "react";
import type { ReactElement, ReactNode } from "react";

interface CodeChildProps {
  className?: string;
  children?: ReactNode;
}

interface PreProps {
  children?: ReactNode;
}

const LANG_RE = /language-([a-zA-Z0-9_+-]+(?::[^\s]+)?)/;

// Collapse heuristic: anything past this height starts collapsed
// so a 400-line dump doesn't push the user's reply offscreen. The
// collapsed view shows the first COLLAPSE_VISIBLE_LINES rows; the
// user expands with the toggle at the bottom.
const COLLAPSE_THRESHOLD_LINES = 30;
const COLLAPSE_VISIBLE_LINES = 20;

export function CodeBlock({ children }: PreProps) {
  const codeChild = findCodeChild(children);
  const className = codeChild?.props.className ?? "";
  const langMatch = LANG_RE.exec(className);
  const rawTag = langMatch?.[1] ?? "";
  const { label, lang } = parseLanguageTag(rawTag);

  const codeText = extractText(codeChild?.props.children);
  const lineCount = codeText.replace(/\n$/, "").split("\n").length;
  const canCollapse = lineCount > COLLAPSE_THRESHOLD_LINES;

  const [expanded, setExpanded] = useState(false);
  const collapsed = canCollapse && !expanded;

  return (
    <div className={`code-block not-prose${collapsed ? " is-collapsed" : ""}`}>
      <div className="code-block-header">
        <span className="code-block-lang" title={lang || undefined}>
          {label}
        </span>
        <CopyButton text={codeText} />
      </div>
      <div className="code-block-body scrollbar-thin">
        <pre className="code-block-content">
          {codeChild ?? <code>{codeText}</code>}
        </pre>
      </div>
      {canCollapse ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="code-block-toggle focus-ring"
          aria-expanded={expanded}
        >
          {expanded
            ? "Show less"
            : `Show more (${lineCount - COLLAPSE_VISIBLE_LINES} more lines)`}
        </button>
      ) : null}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — best effort */
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      className={`code-block-copy focus-ring ${copied ? "is-copied" : ""}`}
      aria-label={copied ? "Copied" : "Copy code"}
    >
      {copied ? (
        <>
          <svg
            width={14}
            height={14}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M5 13l4 4L19 7" />
          </svg>
          Copied!
        </>
      ) : (
        <>
          <svg
            width={14}
            height={14}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.6}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V6a2 2 0 0 1 2-2h9" />
          </svg>
          Copy
        </>
      )}
    </button>
  );
}

// parseLanguageTag turns a raw markdown language token into a
// display label + a hint for the title attribute. Three shapes:
//
//   ""                 → { label: "Code", lang: "" }
//   "python"           → { label: "Python", lang: "python" }
//   "javascript:app.js" → { label: "app.js", lang: "javascript" }
//
// When the tag carries a filename we trust it as-is (filenames are
// case-sensitive and may contain dots/extensions); when only a
// language is present we capitalize the first letter so the header
// reads as a noun ("JavaScript", "Python") instead of a switch
// keyword.
function parseLanguageTag(raw: string): { label: string; lang: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { label: "Code", lang: "" };
  const colon = trimmed.indexOf(":");
  if (colon !== -1) {
    const lang = trimmed.slice(0, colon);
    const filename = trimmed.slice(colon + 1).trim();
    if (filename) {
      return { label: filename, lang };
    }
  }
  return { label: capitalize(trimmed), lang: trimmed };
}

function capitalize(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function findCodeChild(children: ReactNode): ReactElement<CodeChildProps> | null {
  const arr = Children.toArray(children);
  for (const c of arr) {
    if (
      isValidElement(c) &&
      (c.type === "code" ||
        (c.props as CodeChildProps)?.className?.includes("language-"))
    ) {
      return c as ReactElement<CodeChildProps>;
    }
  }
  return null;
}

function extractText(node: ReactNode): string {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (isValidElement(node)) {
    return extractText((node.props as { children?: ReactNode })?.children);
  }
  return "";
}
