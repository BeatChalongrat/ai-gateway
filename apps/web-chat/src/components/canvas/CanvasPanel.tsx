"use client";

// Phase 6 / C1 — read-only Canvas side panel. Renders a STATIC
// preview of an artifact the model produced (HTML / SVG / Markdown).
// No editing (C3), no script execution (C2): see the security model
// in canvasUtil.ts — every HTML/SVG preview goes through an
// <iframe sandbox=""> (empty allowlist) with a default-src 'none'
// CSP baked into the srcdoc. Model markup is NEVER injected into the
// app's own DOM (no dangerouslySetInnerHTML anywhere in this folder —
// pinned by CanvasPanel.security.test.tsx).
//
// Layout: same modal slide-over drawer pattern as WorkspacePanel
// (backdrop z-40 + fixed right aside z-50). The two drawers cannot
// fight: each has a full-screen backdrop, and every "Open in Canvas"
// button lives in the chat surface UNDER the workspace backdrop, so
// only one drawer is reachable at a time.

import { useEffect, useState } from "react";

import { Download, PanelRight, X } from "lucide-react";

import { useT } from "@/lib/i18n";
import { useCanvasStore } from "@/stores/canvasStore";
import type { CanvasArtifact } from "@/stores/canvasStore";

import { Markdown } from "@/components/chat/Markdown";
import {
  MAX_CANVAS_CHARS,
  buildHtmlSrcDoc,
  buildSvgSrcDoc,
  canvasDownloadName,
} from "./canvasUtil";

const TYPE_LABEL_KEY = {
  html: "canvas.typeHtml",
  svg: "canvas.typeSvg",
  markdown: "canvas.typeMarkdown",
} as const;

const DOWNLOAD_MIME = {
  html: "text/html",
  svg: "image/svg+xml",
  markdown: "text/markdown",
} as const;

export function CanvasPanel() {
  const t = useT();
  const open = useCanvasStore((s) => s.open);
  const artifact = useCanvasStore((s) => s.artifact);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);

  // ESC to close — same pattern as WorkspacePanel.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeCanvas();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, closeCanvas]);

  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-200 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={closeCanvas}
        aria-hidden
      />
      <aside
        className={`fixed right-0 top-0 z-50 flex h-full w-full flex-col border-l border-border-default bg-bg-surface shadow-2xl transition-transform duration-200 ease-out md:max-w-2xl ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        role="dialog"
        aria-label={t("canvas.title")}
        data-testid="canvas-panel"
      >
        <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-3">
          <PanelRight className="h-5 w-5 text-text-tertiary" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-text-primary">
              {artifact?.title || t("canvas.title")}
            </div>
            <div className="text-xs text-text-tertiary">
              {artifact ? t(TYPE_LABEL_KEY[artifact.type]) : null}
              {" · "}
              {t("canvas.readOnly")}
            </div>
          </div>
          {artifact ? (
            <>
              <CopyArtifactButton content={artifact.content} />
              <DownloadArtifactButton artifact={artifact} />
            </>
          ) : null}
          <button
            type="button"
            onClick={closeCanvas}
            className="focus-ring rounded p-1 text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary"
            aria-label={t("canvas.close")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-hidden">
          {open ? <CanvasBody artifact={artifact} /> : null}
        </div>
      </aside>
    </>
  );
}

function CanvasBody({ artifact }: { artifact: CanvasArtifact | null }) {
  const t = useT();
  if (!artifact || !artifact.content.trim()) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-text-tertiary">
        {t("canvas.empty")}
      </div>
    );
  }
  if (artifact.content.length > MAX_CANVAS_CHARS) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center"
        data-testid="canvas-too-large"
      >
        <div className="text-sm text-text-secondary">{t("canvas.tooLarge")}</div>
        <DownloadArtifactButton artifact={artifact} labeled />
      </div>
    );
  }
  switch (artifact.type) {
    case "html":
      return (
        <SandboxedFrame
          srcDoc={buildHtmlSrcDoc(artifact.content)}
          title={t("canvas.typeHtml")}
        />
      );
    case "svg":
      return (
        <SandboxedFrame
          srcDoc={buildSvgSrcDoc(artifact.content)}
          title={t("canvas.typeSvg")}
        />
      );
    case "markdown":
      return (
        <div className="prose-chat h-full overflow-y-auto px-5 py-4">
          <Markdown>{artifact.content}</Markdown>
        </div>
      );
  }
}

// The ONLY way model HTML/SVG reaches pixels: an iframe whose sandbox
// is the EMPTY allowlist. Do not add tokens — `sandbox=""` (no
// allow-scripts, no allow-same-origin) plus the srcdoc CSP is the C1
// security contract, pinned by tests.
function SandboxedFrame({ srcDoc, title }: { srcDoc: string; title: string }) {
  return (
    <iframe
      sandbox=""
      srcDoc={srcDoc}
      title={title}
      referrerPolicy="no-referrer"
      loading="lazy"
      className="h-full w-full border-0 bg-white"
      data-testid="canvas-frame"
    />
  );
}

function CopyArtifactButton({ content }: { content: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(content);
      } else {
        const ta = document.createElement("textarea");
        ta.value = content;
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
      className="focus-ring rounded p-1 text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      aria-label={copied ? t("code.copied") : t("code.copyAria")}
      title={copied ? t("code.copied") : t("code.copy")}
    >
      {copied ? (
        <svg
          width={16}
          height={16}
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
      ) : (
        <svg
          width={16}
          height={16}
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
      )}
    </button>
  );
}

function DownloadArtifactButton({
  artifact,
  labeled = false,
}: {
  artifact: CanvasArtifact;
  labeled?: boolean;
}) {
  const t = useT();

  function download() {
    const blob = new Blob([artifact.content], {
      type: DOWNLOAD_MIME[artifact.type],
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = canvasDownloadName(artifact.type, artifact.title);
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  if (labeled) {
    return (
      <button
        type="button"
        onClick={download}
        className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border-default bg-bg-elevated px-3 py-1.5 text-sm text-text-primary transition-colors hover:bg-bg-surface"
      >
        <Download className="h-4 w-4" />
        {t("canvas.download")}
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={download}
      className="focus-ring rounded p-1 text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      aria-label={t("canvas.download")}
      title={t("canvas.download")}
    >
      <Download className="h-4 w-4" />
    </button>
  );
}
