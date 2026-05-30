"use client";

// Shared assistant-markdown renderer used by BOTH the live-streaming
// path (StreamingContent) and the completed-message path
// (MessageBubble's AssistantContent). Keeping a single renderer means
// streaming output and the final render are byte-for-byte identical —
// no "snap" when the message completes (beyond plan-card extraction,
// which is handled by the caller).
//
// Pipeline: remark-gfm (tables / task-lists / strikethrough /
// autolinks / hr) + rehype-highlight (code syntax) + a small set of
// component overrides (citation pills, CodeBlock). Styling comes from
// the ``.prose-chat`` wrapper the caller mounts around us.
//
// Incomplete markdown is handled gracefully by CommonMark itself: an
// unclosed ``` fence renders as a code block to end-of-buffer, an
// unclosed ** stays literal, a half-typed table shows its raw pipes
// until the delimiter row arrives — nothing breaks the whole message.

import { useEffect, useMemo, useState } from "react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Components } from "react-markdown";
import type { PluggableList } from "unified";

import { ImageOff } from "lucide-react";

import { openLightbox } from "@/components/ui/ImageLightbox";
import { CodeBlock } from "./CodeBlock";

// ── Lazy math (KaTeX) ──────────────────────────────────────────
// KaTeX (JS + CSS + fonts) is sizeable, so we load remark-math +
// rehype-katex + the stylesheet ON DEMAND — only the first time a
// message that actually contains math renders. Loaded once per
// session and cached at module scope; subsequent math messages reuse
// it synchronously. Until the chunk resolves, math shows as its raw
// ``$...$`` source (one tick), then re-renders formatted.
interface MathPlugins {
  remark: PluggableList[number];
  rehype: PluggableList[number];
}
let cachedMath: MathPlugins | null = null;
let mathPromise: Promise<MathPlugins> | null = null;

function loadMathPlugins(): Promise<MathPlugins> {
  if (cachedMath) return Promise.resolve(cachedMath);
  if (!mathPromise) {
    mathPromise = Promise.all([
      import("remark-math"),
      import("rehype-katex"),
      // Side-effect import — webpack code-splits the CSS into this
      // async chunk so it isn't in the main/route bundle.
      import("katex/dist/katex.min.css"),
    ]).then(([rm, rk]) => {
      cachedMath = {
        remark: rm.default,
        // throwOnError:false → invalid LaTeX renders as a red error
        // span instead of blowing up the whole message render.
        rehype: [rk.default, { throwOnError: false }],
      };
      return cachedMath;
    });
  }
  return mathPromise;
}

// hasMath — cheap gate deciding whether to LOAD katex (not whether to
// render math; remark-math itself decides that per CommonMark-math
// rules). Over-approximates: a ``$$`` block or a ``$x…$`` pair. A
// false positive on prose like "$5+$10" only wastes a one-time lazy
// load; "$100 and $200" (spaces) won't render as math regardless.
function hasMath(text: string): boolean {
  return text.includes("$$") || /\$[^\s$][^$]*\$/.test(text);
}

// Citation marker — RAG context tells the model to cite sources using
// the literal pattern ``[Source: <filename>]``. We post-process the
// assistant text to convert that pattern into a markdown link with a
// special internal href scheme; the custom anchor renderer below
// detects that scheme and renders a pill instead of a regular link.
const CITATION_PREFIX = "#__rag_citation:";
const CITATION_PATTERN = /\[Source:\s*([^\]]+?)\]/g;

// renderCitations rewrites ``[Source: x]`` into a hidden-scheme link
// the anchor override turns into a pill. Exported so the completed
// path can pre-process per plan-segment if it ever needs to; today
// ChatMarkdown applies it internally.
export function renderCitations(text: string): string {
  return text.replace(CITATION_PATTERN, (_, raw: string) => {
    const filename = raw.trim();
    const safe = filename.replace(/\(/g, "%28").replace(/\)/g, "%29");
    return `[__RAG_CITATION__${filename}](${CITATION_PREFIX}${safe})`;
  });
}

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

// MarkdownImage renders an assistant ``![alt](url)`` image: clamped to
// the column width, rounded, click-to-zoom via the shared lightbox.
// A failed load (the URL doesn't resolve) degrades to a labeled
// placeholder instead of a broken-image icon. These are PUBLIC URLs
// (unlike uploaded files) so they load directly — no auth/proxy.
function MarkdownImage({ src, alt }: { src?: string; alt?: string }) {
  const [failed, setFailed] = useState(false);
  if (!src) return null;
  if (failed) {
    return (
      <span className="my-1 inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-elevated px-2.5 py-1.5 text-xs text-text-tertiary">
        <ImageOff className="h-4 w-4 shrink-0" aria-hidden />
        {alt || "Image unavailable"}
      </span>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={src}
      alt={alt ?? ""}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      onClick={() => openLightbox(src, alt)}
      className="my-2 max-w-full cursor-zoom-in rounded-lg border border-border"
    />
  );
}

// buildComponents returns the react-markdown component overrides.
// ``streaming`` is forwarded to CodeBlock, which uses it to (a) keep a
// growing code block from collapsing mid-stream and (b) hold back
// Mermaid diagram rendering until the source is complete.
function buildComponents(streaming: boolean): Components {
  return {
    a: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
      const href = props.href ?? "";
      if (href.startsWith(CITATION_PREFIX)) {
        const filename = decodeURIComponent(href.slice(CITATION_PREFIX.length));
        return <CitationPill filename={filename} />;
      }
      return <a {...props} target="_blank" rel="noopener noreferrer" />;
    },
    pre: (props: { children?: React.ReactNode }) => (
      <CodeBlock {...props} streaming={streaming} />
    ),
    img: (props: React.ImgHTMLAttributes<HTMLImageElement>) => (
      <MarkdownImage src={props.src} alt={props.alt} />
    ),
  };
}

export function ChatMarkdown({
  text,
  streaming = false,
}: {
  text: string;
  streaming?: boolean;
}) {
  const components = useMemo(() => buildComponents(streaming), [streaming]);

  // Content-gated lazy math. wantMath gates the (cheap) import trigger;
  // math holds the resolved plugins once loaded.
  const wantMath = hasMath(text);
  const [math, setMath] = useState<MathPlugins | null>(cachedMath);
  useEffect(() => {
    if (!wantMath || math) return;
    let alive = true;
    loadMathPlugins()
      .then((m) => {
        if (alive) setMath(m);
      })
      .catch(() => {
        /* leave math literal if the chunk fails to load */
      });
    return () => {
      alive = false;
    };
  }, [wantMath, math]);

  const useMath = wantMath && math !== null;
  const remarkPlugins = useMemo<PluggableList>(
    () => (useMath ? [remarkGfm, math!.remark] : [remarkGfm]),
    [useMath, math],
  );
  const rehypePlugins = useMemo<PluggableList>(
    () => (useMath ? [rehypeHighlight, math!.rehype] : [rehypeHighlight]),
    [useMath, math],
  );

  return (
    <ReactMarkdown
      remarkPlugins={remarkPlugins}
      rehypePlugins={rehypePlugins}
      components={components}
    >
      {renderCitations(text)}
    </ReactMarkdown>
  );
}
