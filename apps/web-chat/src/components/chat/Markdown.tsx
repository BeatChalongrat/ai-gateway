"use client";

// Shared Markdown renderer — the ONE place the react-markdown pipeline
// lives (plugins + component overrides). Every markdown render site in
// the app (MessageBubble streaming + completed segments, the public
// /shared/{token} page) MUST go through this component so behaviour
// (GFM tables, syntax highlighting, LaTeX, citation pills, link
// hardening) can never drift between surfaces. A unit test pins this:
// no file other than this one may import react-markdown directly.
//
// LaTeX (Phase 12): remark-math parses $...$ / $$...$$ into math nodes
// and rehype-katex renders them with KaTeX. The old MessageBubble TODO
// deferred this to avoid pulling KaTeX's ~23 kB (gzipped) stylesheet
// into the chat bundle; we resolve that concern by importing the CSS
// here, in the component module, instead of app/layout.tsx — Next.js
// (App Router) code-splits CSS per entry, so the stylesheet ships only
// with routes that actually render markdown (chat + shared view), not
// with login/settings/etc. KaTeX's woff2 fonts are declared via
// @font-face and are only fetched by the browser once a .katex element
// actually renders, so non-math conversations never download them.
//
// SECURITY INVARIANTS (pinned by Markdown.test.tsx — do not relax):
// - NO rehype-raw: raw HTML in markdown (<script>, <img onerror>) is
//   dropped by react-markdown, never parsed into live DOM nodes.
// - KaTeX `trust` stays false (KATEX_OPTIONS below): \href, \url,
//   \includegraphics and other trust-gated commands cannot emit
//   attacker-controlled URLs/attributes.
// - KaTeX `output: "html"` emits KaTeX-generated markup only (no
//   MathML branch); nothing user-authored is ever passed through raw.

import "katex/dist/katex.min.css";

import ReactMarkdown, { type Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";

import { CodeBlock } from "./CodeBlock";

// Citation marker — RAG context tells the model to cite sources using
// the literal pattern ``[Source: <filename>]``. We post-process the
// assistant text to convert that pattern into a markdown link with a
// special internal href scheme; the custom anchor renderer below
// detects that scheme and renders a pill instead of a regular link.
//
// Why a hidden link scheme? It survives every place ReactMarkdown
// passes children through (paragraphs, list items, table cells)
// without us re-implementing the renderer for each node type.
const CITATION_PREFIX = "#__rag_citation:";
const CITATION_PATTERN = /\[Source:\s*([^\]]+?)\]/g;

function renderCitations(text: string): string {
  // Escape any existing parentheses inside the filename so the markdown
  // link grammar parses cleanly. We don't try to be clever — citations
  // come from the indexer which already sanitizes filenames, so the
  // edge cases here are vanishingly rare.
  return text.replace(CITATION_PATTERN, (_, raw: string) => {
    const filename = raw.trim();
    const safe = filename
      .replace(/\(/g, "%28")
      .replace(/\)/g, "%29");
    return `[__RAG_CITATION__${filename}](${CITATION_PREFIX}${safe})`;
  });
}

// KaTeX options, exported so tests can assert the security posture.
// `trust: false` is the KaTeX default but we state it explicitly —
// it is the switch that keeps \href / \includegraphics from injecting
// attacker-controlled URLs, and an explicit value survives refactors
// (and lets the test suite assert it) better than an implicit default.
// rehype-katex itself catches KaTeX parse errors and renders the raw
// source in an error-colored span instead of throwing, so malformed
// math degrades gracefully without any throwOnError plumbing here.
export const KATEX_OPTIONS = {
  trust: false,
  output: "html",
} as const;

const remarkPlugins: Options["remarkPlugins"] = [remarkGfm, remarkMath];
// NOTE: rehype-raw is deliberately absent. Raw HTML must stay inert.
const rehypePlugins: Options["rehypePlugins"] = [
  rehypeHighlight,
  [rehypeKatex, KATEX_OPTIONS],
];

const markdownComponents = {
  a: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
    const href = props.href ?? "";
    if (href.startsWith(CITATION_PREFIX)) {
      const filename = decodeURIComponent(href.slice(CITATION_PREFIX.length));
      return <CitationPill filename={filename} />;
    }
    return <a {...props} target="_blank" rel="noopener noreferrer" />;
  },
  pre: (props: { children?: React.ReactNode }) => <CodeBlock {...props} />,
  // Wrap every GFM table in its own scroll container. The table itself
  // then stays `display: table` so the browser's real column algorithm
  // runs (the old `display: block; overflow-x: auto` on the <table>
  // scrolled, but at the cost of turning the table into a block box —
  // column widths stopped tracking content and the frame could not be
  // rounded). The wrapper owns the border, radius, clipping and the
  // horizontal scroll instead, so a 12-column table scrolls inside the
  // message instead of widening the whole chat column on mobile.
  //
  // This is a plain React element — no HTML string, no dangerouslySet-
  // InnerHTML — so the no-raw-HTML invariant is untouched.
  //
  // tabIndex + role make the scroll area reachable and announceable for
  // keyboard/screen-reader users, which WCAG 2.1.1 requires of any
  // region that only scrolling can reveal.
  table: (props: React.TableHTMLAttributes<HTMLTableElement>) => (
    <div
      className="md-table-wrap scrollbar-thin"
      role="region"
      aria-label="Table"
      tabIndex={0}
    >
      <table {...props} />
    </div>
  ),
};

function CitationPill({ filename }: { filename: string }) {
  return (
    <span
      title={`From knowledge base: ${filename}`}
      className="mx-0.5 inline-flex items-center gap-1 rounded-full border border-border bg-bg-elevated px-2 py-0.5 align-baseline text-[11px] font-medium text-text-secondary"
    >
      <svg
        width={10}
        height={10}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
        <path d="M14 3v6h6" />
      </svg>
      {filename}
    </span>
  );
}

interface MarkdownProps {
  /** Markdown source text (may be mid-stream / truncated). */
  children: string;
  /**
   * When true, ``[Source: file]`` citation markers are rewritten into
   * pill elements before parsing. Chat surfaces enable this; the
   * public shared view keeps the historical plain-text behaviour.
   */
  citations?: boolean;
}

export function Markdown({ children, citations = false }: MarkdownProps) {
  return (
    <ReactMarkdown
      remarkPlugins={remarkPlugins}
      rehypePlugins={rehypePlugins}
      components={markdownComponents}
    >
      {citations ? renderCitations(children) : children}
    </ReactMarkdown>
  );
}
