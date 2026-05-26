"use client";

// Code block container rendered by react-markdown's `pre` override.
// Adds:
//   - language label (top-right of the header bar)
//   - copy button with "Copied!" affordance (2s tooltip)
//   - line numbers when the block has > 5 lines
//
// rehype-highlight has already wrapped the inner <code> with hljs
// language classes, so the highlighting tokens still apply. We just
// give it a styled container.

import { Children, isValidElement, useState } from "react";
import type { ReactElement, ReactNode } from "react";

interface CodeChildProps {
  className?: string;
  children?: ReactNode;
}

interface PreProps {
  children?: ReactNode;
}

const LANG_RE = /language-([a-zA-Z0-9_+-]+)/;

export function CodeBlock({ children }: PreProps) {
  const codeChild = findCodeChild(children);
  const className = codeChild?.props.className ?? "";
  const langMatch = LANG_RE.exec(className);
  const lang = langMatch?.[1] ?? "";

  const codeText = extractText(codeChild?.props.children);
  const lines = codeText.replace(/\n$/, "").split("\n");
  const showLineNumbers = lines.length > 5;

  return (
    <div className="code-block not-prose">
      <div className="code-block-header">
        <span className="code-block-lang">{lang || "text"}</span>
        <CopyButton text={codeText} />
      </div>
      <div className="code-block-body scrollbar-thin">
        {showLineNumbers ? (
          <div className="code-block-lines" aria-hidden>
            {lines.map((_, i) => (
              <span key={i}>{i + 1}</span>
            ))}
          </div>
        ) : null}
        <pre className="code-block-content">
          {codeChild ?? <code>{codeText}</code>}
        </pre>
      </div>
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
      className={`code-block-copy focus-ring ${copied ? "copied" : ""}`}
      aria-label={copied ? "Copied" : "Copy code"}
    >
      {copied ? (
        <>
          <svg width={12} height={12} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M5 13l4 4L19 7"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Copied
        </>
      ) : (
        <>
          <svg width={12} height={12} viewBox="0 0 24 24" fill="none" aria-hidden>
            <rect
              x="9"
              y="9"
              width="11"
              height="11"
              rx="2"
              stroke="currentColor"
              strokeWidth="1.7"
            />
            <path
              d="M5 15V6a2 2 0 0 1 2-2h9"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
            />
          </svg>
          Copy
        </>
      )}
    </button>
  );
}

function findCodeChild(children: ReactNode): ReactElement<CodeChildProps> | null {
  const arr = Children.toArray(children);
  for (const c of arr) {
    if (isValidElement(c) && (c.type === "code" || (c.props as CodeChildProps)?.className?.includes("language-"))) {
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
