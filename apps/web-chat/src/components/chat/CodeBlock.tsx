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

import { Children, isValidElement, useCallback, useEffect, useRef, useState } from "react";
import type { ReactElement, ReactNode } from "react";

import {
  artifactKeyFor,
  useCanvasSource,
} from "@/components/canvas/CanvasSourceContext";
import { buildHtmlSrcDoc, buildSvgSrcDoc, canvasTypeForBlock, htmlNeedsScripts } from "@/components/canvas/canvasUtil";
import { useT } from "@/lib/i18n";
import { useCanvasStore } from "@/stores/canvasStore";
import type { CanvasArtifactType } from "@/stores/canvasStore";

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
  const t = useT();
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
  const { bodyRef, moreRight, onBodyScroll } = useMoreToTheRight(codeText);

  // Phase 6 / C1 — previewable blocks (html/svg/markdown) get an
  // "Open in Canvas" button NEXT TO copy, never replacing it. The
  // inline code block stays the default render; the canvas is opt-in.
  //
  // FIX82 ② — ONE Open affordance per block (the FIX77 rule, different
  // widget: a block fully presented by a card needs no second button).
  // Script-needing html renders the FIX81 card, which explains why there
  // is no preview and carries the action — so the header button hides
  // for exactly that case and stays for everything else (svg and
  // script-free html with their inline frames, markdown/mermaid/react
  // with no card at all). Copy is untouched.
  const canvasType = canvasTypeForBlock(lang, codeText);
  const interactiveHtml = canvasType === "html" && htmlNeedsScripts(codeText);
  const filename = label !== "Code" && label.includes(".") ? label : undefined;

  return (
    <div className={`code-block not-prose${collapsed ? " is-collapsed" : ""}`}>
      <div className="code-block-header">
        <span className="code-block-lang" title={lang || undefined}>
          {label === "Code" && !lang ? t("code.label") : label}
        </span>
        {canvasType && !interactiveHtml ? (
          <OpenCanvasButton
            type={canvasType}
            content={codeText}
            title={filename}
          />
        ) : null}
        <CopyButton text={codeText} />
      </div>
      {/* FIX29 §2.3 (defect #23): an html / svg artifact RENDERS in the thread.
          Static posture only (sandbox="" — no scripts, no network, opaque
          origin): the executable render stays behind "Open in Canvas", which
          is the expand-to-full-size action, not the price of admission.

          FIX81 Part B — that holds ONLY for content that needs no script.
          An interactive page rendered script-less is not a smaller version
          of the thing, it is a broken version of it (the owner's game: a
          start button that does nothing). Script-dependent html gets a card
          that opens the canvas — where allow-scripts is granted
          deliberately, behind the user's click — and the raw source keeps
          rendering below as before. SVG stays inline (sanitizeSvg strips
          script by design, so a script-less frame is a faithful render). */}
      {canvasType === "svg" || (canvasType === "html" && !interactiveHtml) ? (
        <div className="code-block-preview border-b border-border-subtle bg-white" data-testid="inline-artifact">
          <iframe
            sandbox=""
            srcDoc={canvasType === "svg" ? buildSvgSrcDoc(codeText) : buildHtmlSrcDoc(codeText)}
            title={filename ?? t("code.previewTitle", { kind: canvasType })}
            referrerPolicy="no-referrer"
            loading="lazy"
            className="h-72 w-full border-0"
          />
        </div>
      ) : canvasType === "html" ? (
        <InteractiveArtifactCard content={codeText} title={filename} />
      ) : null}
      {/* FIX132 F32 — the body is the one horizontal scroller (see
          .code-block-content code.hljs in globals.css), and a fade on its
          right edge says a line goes on (NIGHT3 j12-02: a 1,648-px line in
          a 294-px box, no scrollbar, fade or arrow). */}
      <div className="relative">
        <div ref={bodyRef} onScroll={onBodyScroll} className="code-block-body scrollbar-thin">
          <pre className="code-block-content">
            {codeChild ?? <code>{codeText}</code>}
          </pre>
        </div>
        {moreRight ? <div aria-hidden className="code-scroll-hint" data-testid="code-scroll-hint" /> : null}
      </div>
      {canCollapse ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="code-block-toggle focus-ring"
          aria-expanded={expanded}
        >
          {expanded
            ? t("code.showLess")
            : t("code.showMore", { n: String(lineCount - COLLAPSE_VISIBLE_LINES) })}
        </button>
      ) : null}
    </div>
  );
}

// FIX81 Part B — the card an INTERACTIVE html block gets instead of a
// dead script-less thumbnail: name, what it is, and one action that opens
// the canvas (the surface that grants allow-scripts deliberately, behind
// the user's click — FIX76 docks it beside the chat, so opening no longer
// costs the conversation). The raw source keeps rendering below.
function InteractiveArtifactCard({ content, title }: { content: string; title?: string }) {
  const t = useT();
  const openCanvas = useCanvasStore((s) => s.openCanvas);
  const source = useCanvasSource();
  const open = () =>
    openCanvas({
      type: "html",
      content,
      title,
      conversationId: source?.conversationId,
      messageId: source?.messageId,
      artifactKey: source ? artifactKeyFor(source.messageId, content) : undefined,
    });
  return (
    <button
      type="button"
      onClick={open}
      data-testid="interactive-artifact-card"
      className="focus-ring flex w-full items-center gap-3 border-b border-border-subtle bg-bg-surface px-4 py-3 text-left hover:bg-bg-elevated"
    >
      <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0 text-text-muted">
        <rect x="2" y="3" width="20" height="14" rx="2" />
        <path d="m10 8 4 2.5-4 2.5z" fill="currentColor" stroke="none" />
        <path d="M8 21h8M12 17v4" />
      </svg>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-text-primary">{title ?? t("code.interactiveTitle")}</span>
        <span className="block text-xs text-text-secondary">{t("code.interactiveHint")}</span>
      </span>
      <span className="shrink-0 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-text-secondary">{t("canvas.open")}</span>
    </button>
  );
}

// Phase 6 / C1 — opens the read-only Canvas panel with this block's
// content. Only rendered for canvas-eligible types.
function OpenCanvasButton({
  type,
  content,
  title,
}: {
  type: CanvasArtifactType;
  content: string;
  title?: string;
}) {
  const t = useT();
  const openCanvas = useCanvasStore((s) => s.openCanvas);
  // C3 — bind the artifact to its conversation/message so edits can be
  // saved as versions. Outside a chat message (public shared view) the
  // source is null and the canvas opens in session-only mode.
  const source = useCanvasSource();
  return (
    <button
      type="button"
      onClick={() =>
        openCanvas({
          type,
          content,
          title,
          conversationId: source?.conversationId,
          messageId: source?.messageId,
          artifactKey: source
            ? artifactKeyFor(source.messageId, content)
            : undefined,
        })
      }
      className="code-block-copy focus-ring"
      aria-label={t("canvas.open")}
    >
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
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M15 3v18" />
      </svg>
      {t("canvas.open")}
    </button>
  );
}

function CopyButton({ text }: { text: string }) {
  const t = useT();
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
      aria-label={copied ? t("code.copied") : t("code.copyAria")}
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
          {t("code.copied")}
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
          {t("code.copy")}
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

/** FIX132 F32 — is part of the block's content off to the right? Re-read on
 *  scroll, on resize and when the code changes (streaming). */
function useMoreToTheRight(content: string) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [moreRight, setMoreRight] = useState(false);
  const measure = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    setMoreRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);
  useEffect(() => {
    measure();
    const el = bodyRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, content]);
  return { bodyRef, moreRight, onBodyScroll: measure };
}
