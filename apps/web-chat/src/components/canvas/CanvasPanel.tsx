"use client";

// Phase 6 — Canvas side panel. Renders an artifact the model produced
// (HTML / React / mermaid / SVG / Markdown) and, since C3, lets the
// user edit it and keep versions.
//
// Security model (canvasUtil.ts has the full rationale):
//   - EXECUTABLE artifacts (html/react/mermaid) render in an iframe
//     sandboxed EXACTLY "allow-scripts" — no allow-same-origin, so the
//     document is an opaque origin and its JS cannot reach parent DOM,
//     cookies or storage — plus a srcdoc CSP with connect-src 'none'.
//   - STATIC artifacts (svg) keep the C1 posture: sandbox="" (scripts
//     cannot run at all) after string sanitising.
//   - C3 editing changes only WHICH SOURCE is rendered; it reuses the
//     exact same builders + frames, so the posture cannot drift.
//   - Model markup is NEVER injected into the app's own DOM (no
//     dangerouslySetInnerHTML anywhere in this folder — pinned by a
//     source-scan test).
//
// Layout (FIX76): the shared SidePane frame — docked beside the thread
// at ≥ lg with the conversation still live, a slide-over dialog below
// that. One pane slot for the app (sidePaneStore): opening the canvas
// replaces an open workspace/preview pane and vice versa.

import { useEffect, useRef, useState } from "react";

import {
  AlertTriangle,
  Code2,
  Download,
  Eye,
  History,
  Loader2,
  PanelRight,
  RotateCcw,
  Save,
  X,
} from "lucide-react";

import { useT } from "@/lib/i18n";
import { SidePane } from "@/components/ui/SidePane";
import { canPersist, useCanvasStore } from "@/stores/canvasStore";
import type { CanvasArtifact } from "@/stores/canvasStore";

import { Markdown } from "@/components/chat/Markdown";
import {
  CANVAS_EXEC_SANDBOX,
  CANVAS_MSG_MARKER,
  MAX_CANVAS_CHARS,
  buildHtmlSrcDoc,
  buildMermaidSrcDoc,
  buildReactSrcDoc,
  buildSvgSrcDoc,
  canvasDownloadName,
} from "./canvasUtil";

const TYPE_LABEL_KEY = {
  html: "canvas.typeHtml",
  svg: "canvas.typeSvg",
  markdown: "canvas.typeMarkdown",
  mermaid: "canvas.typeMermaid",
  react: "canvas.typeReact",
} as const;

const DOWNLOAD_MIME = {
  html: "text/html",
  svg: "image/svg+xml",
  markdown: "text/markdown",
  mermaid: "text/plain",
  react: "text/plain",
} as const;

// How long an executable artifact may stay silent (no "ready" from the
// bootstrap) before we surface the slow-load hint. The frame is NOT
// torn down — big artifacts may legitimately still be booting.
const CANVAS_READY_TIMEOUT_MS = 12_000;

// C3 — how long typing must pause before the live preview re-renders.
// Each re-render remounts the sandboxed frame, so this trades keystroke
// latency for not thrashing the artifact's own boot.
export const CANVAS_EDIT_DEBOUNCE_MS = 500;

export function CanvasPanel() {
  const t = useT();
  const open = useCanvasStore((s) => s.open);
  const artifact = useCanvasStore((s) => s.artifact);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const draft = useCanvasStore((s) => s.draft);
  const mode = useCanvasStore((s) => s.mode);
  const setMode = useCanvasStore((s) => s.setMode);
  const dirty = useCanvasStore((s) => s.dirty);
  const saving = useCanvasStore((s) => s.saving);
  const error = useCanvasStore((s) => s.error);
  const saveVersion = useCanvasStore((s) => s.saveVersion);
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    if (!open) setHistoryOpen(false);
  }, [open]);

  // Esc, backdrop, focus and the dialog/landmark role live in SidePane.
  return (
    <SidePane
      open={open}
      label={t("canvas.title")}
      title={artifact?.title || t("canvas.title")}
      subtitle={
        artifact ? (
          <>
            {t(TYPE_LABEL_KEY[artifact.type])}
            {dirty ? ` · ${t("canvas.unsaved")}` : null}
          </>
        ) : null
      }
      icon={<PanelRight className="h-5 w-5 text-text-tertiary" />}
      actions={
        artifact ? (
          <>
            <CopyArtifactButton content={draft} />
            <DownloadArtifactButton artifact={artifact} content={draft} />
          </>
        ) : null
      }
      maxWidth={672}
      closeLabel={t("canvas.close")}
      onClose={closeCanvas}
      testId="canvas-panel"
    >

        {/* C3 toolbar — Preview/Code toggle + save + history. Hidden
            for the empty state and for artifacts too large to edit. */}
        {artifact && artifact.content.length <= MAX_CANVAS_CHARS ? (
          <div className="flex items-center gap-1 border-b border-border-subtle px-3 py-1.5">
            <div className="flex overflow-hidden rounded-lg border border-border-default">
              <ModeButton
                active={mode === "preview"}
                onClick={() => setMode("preview")}
                icon={<Eye className="h-3.5 w-3.5" />}
                label={t("canvas.modePreview")}
              />
              <ModeButton
                active={mode === "code"}
                onClick={() => setMode("code")}
                icon={<Code2 className="h-3.5 w-3.5" />}
                label={t("canvas.modeCode")}
              />
            </div>
            <div className="flex-1" />
            {canPersist(artifact) ? (
              <>
                <button
                  type="button"
                  onClick={() => void saveVersion()}
                  disabled={!dirty || saving}
                  className="focus-ring inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:opacity-40"
                  data-testid="canvas-save"
                >
                  {saving ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Save className="h-3.5 w-3.5" />
                  )}
                  {t("canvas.save")}
                </button>
                <button
                  type="button"
                  onClick={() => setHistoryOpen((v) => !v)}
                  className={`focus-ring inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs transition-colors hover:bg-bg-elevated ${
                    historyOpen ? "text-text-primary" : "text-text-secondary"
                  }`}
                  aria-expanded={historyOpen}
                  data-testid="canvas-history-toggle"
                >
                  <History className="h-3.5 w-3.5" />
                  {t("canvas.history")}
                </button>
              </>
            ) : (
              <span className="px-2 text-[11px] text-text-tertiary">
                {t("canvas.sessionOnly")}
              </span>
            )}
          </div>
        ) : null}

        {error ? (
          <div
            className="border-b border-border-subtle bg-red-500/10 px-4 py-2 text-xs text-red-500"
            data-testid="canvas-save-error"
          >
            {error}
          </div>
        ) : null}

        {historyOpen ? <VersionRail /> : null}

        <div className="flex-1 overflow-hidden">
          {open ? <CanvasBody artifact={artifact} /> : null}
        </div>
    </SidePane>
  );
}

function ModeButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs transition-colors ${
        active
          ? "bg-bg-elevated text-text-primary"
          : "text-text-secondary hover:text-text-primary"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

// C3 — saved-version rail. Deliberately NOT the Workspace
// VersionTimeline/ComparisonCard: those model generated FILES (size
// deltas, sheet diffs, download URLs) and don't fit a text artifact.
function VersionRail() {
  const t = useT();
  const versions = useCanvasStore((s) => s.versions);
  const loading = useCanvasStore((s) => s.versionsLoading);
  const saving = useCanvasStore((s) => s.saving);
  const revertTo = useCanvasStore((s) => s.revertTo);

  return (
    <div
      className="max-h-40 overflow-y-auto border-b border-border-subtle bg-bg-elevated/40 px-3 py-2"
      data-testid="canvas-versions"
    >
      {loading ? (
        <div className="flex items-center gap-2 px-1 py-1 text-xs text-text-tertiary">
          <Loader2 className="h-3 w-3 animate-spin" />
          {t("canvas.historyLoading")}
        </div>
      ) : versions.length === 0 ? (
        <div className="px-1 py-1 text-xs text-text-tertiary">
          {t("canvas.historyEmpty")}
        </div>
      ) : (
        <ul className="space-y-0.5">
          {versions.map((v, i) => (
            <li
              key={v.id}
              className="flex items-center gap-2 rounded px-1 py-1 text-xs"
            >
              <span className="font-medium text-text-primary">v{v.version}</span>
              <span className="truncate text-text-tertiary">
                {new Date(v.created_at).toLocaleString()}
              </span>
              {i === 0 ? (
                <span className="rounded bg-bg-surface px-1.5 py-0.5 text-[10px] text-text-secondary">
                  {t("canvas.versionCurrent")}
                </span>
              ) : (
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => void revertTo(v.version)}
                  className="focus-ring ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-text-secondary transition-colors hover:bg-bg-surface hover:text-text-primary disabled:opacity-40"
                >
                  <RotateCcw className="h-3 w-3" />
                  {t("canvas.revert")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CanvasBody({ artifact }: { artifact: CanvasArtifact | null }) {
  const t = useT();
  const mode = useCanvasStore((s) => s.mode);
  const draft = useCanvasStore((s) => s.draft);
  const setDraft = useCanvasStore((s) => s.setDraft);
  // Debounced mirror of the draft: the preview re-renders only after
  // typing pauses, so every keystroke doesn't remount the sandbox.
  const [rendered, setRendered] = useState(draft);
  useEffect(() => {
    const id = window.setTimeout(
      () => setRendered(draft),
      CANVAS_EDIT_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(id);
  }, [draft]);

  if (mode === "code" && artifact) {
    return (
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        spellCheck={false}
        aria-label={t("canvas.modeCode")}
        data-testid="canvas-editor"
        className="h-full w-full resize-none border-0 bg-bg-base p-4 font-mono text-xs leading-relaxed text-text-primary outline-none"
      />
    );
  }
  // Preview renders the (debounced) EDIT BUFFER, so an edit shows live.
  // It goes through the very same builders + frames as C1/C2 — C3 adds
  // no second render path, so the sandbox posture cannot drift.
  const source = rendered;
  if (!artifact || !source.trim()) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-text-tertiary">
        {t("canvas.empty")}
      </div>
    );
  }
  if (source.length > MAX_CANVAS_CHARS) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center"
        data-testid="canvas-too-large"
      >
        <div className="text-sm text-text-secondary">{t("canvas.tooLarge")}</div>
        <DownloadArtifactButton artifact={artifact} content={source} labeled />
      </div>
    );
  }
  switch (artifact.type) {
    case "html":
      return (
        <ExecutableFrame
          srcDoc={buildHtmlSrcDoc(source)}
          title={t("canvas.typeHtml")}
        />
      );
    case "react":
      return (
        <ExecutableFrame
          srcDoc={buildReactSrcDoc(source)}
          title={t("canvas.typeReact")}
        />
      );
    case "mermaid":
      return (
        <ExecutableFrame
          srcDoc={buildMermaidSrcDoc(source)}
          title={t("canvas.typeMermaid")}
        />
      );
    case "svg":
      return (
        <StaticFrame
          srcDoc={buildSvgSrcDoc(source)}
          title={t("canvas.typeSvg")}
        />
      );
    case "markdown":
      return (
        <div className="prose-chat h-full overflow-y-auto px-5 py-4">
          <Markdown>{source}</Markdown>
        </div>
      );
  }
}

// C1 static frame — kept for SVG, which never needs scripts: sandbox
// is the EMPTY allowlist (no allow-scripts, no allow-same-origin).
function StaticFrame({ srcDoc, title }: { srcDoc: string; title: string }) {
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

// C2 executable frame — the ONLY place artifact scripts run. Sandbox
// is EXACTLY "allow-scripts": no allow-same-origin (opaque origin —
// running JS still cannot reach parent DOM/cookies/storage), no
// top-navigation, no popups, no forms, no modals. Pinned by tests.
//
// The srcdoc bootstrap postMessages {__sranCanvas, type: ready|error}
// which drives the loading overlay + runtime-error banner. postMessage
// is spoofable by other windows in principle; the impact is limited to
// a cosmetic banner, so the marker + (when available) source check is
// proportionate.
function ExecutableFrame({ srcDoc, title }: { srcDoc: string; title: string }) {
  const t = useT();
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "slow">("loading");
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    setPhase("loading");
    setErrors([]);
    const onMessage = (e: MessageEvent) => {
      const data = e.data as
        | { [CANVAS_MSG_MARKER]?: boolean; type?: string; payload?: string | null }
        | undefined;
      if (!data || data[CANVAS_MSG_MARKER] !== true) return;
      const fw = frameRef.current?.contentWindow;
      if (fw && e.source && e.source !== fw) return;
      if (data.type === "ready") {
        setPhase("ready");
      } else if (data.type === "error") {
        setErrors((prev) =>
          prev.length >= 3 ? prev : [...prev, data.payload || "Script error"],
        );
      }
    };
    window.addEventListener("message", onMessage);
    const timer = window.setTimeout(() => {
      setPhase((p) => (p === "loading" ? "slow" : p));
    }, CANVAS_READY_TIMEOUT_MS);
    return () => {
      window.removeEventListener("message", onMessage);
      window.clearTimeout(timer);
    };
  }, [srcDoc]);

  return (
    <div className="relative flex h-full flex-col">
      {errors.length > 0 ? (
        <div
          className="flex items-start gap-2 border-b border-border-subtle bg-red-500/10 px-4 py-2 text-xs text-red-500"
          data-testid="canvas-runtime-error"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <div className="min-w-0">
            <div className="font-medium">{t("canvas.runtimeError")}</div>
            <div className="truncate opacity-80">{errors[0]}</div>
          </div>
        </div>
      ) : null}
      {phase === "slow" ? (
        <div
          className="border-b border-border-subtle bg-amber-500/10 px-4 py-2 text-xs text-amber-600"
          data-testid="canvas-slow"
        >
          {t("canvas.slowLoad")}
        </div>
      ) : null}
      <div className="relative flex-1">
        {phase === "loading" ? (
          <div
            className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center gap-2 text-sm text-text-tertiary"
            data-testid="canvas-loading"
          >
            <Loader2 className="h-4 w-4 animate-spin" />
            {t("canvas.running")}
          </div>
        ) : null}
        <iframe
          ref={frameRef}
          sandbox={CANVAS_EXEC_SANDBOX}
          srcDoc={srcDoc}
          title={title}
          referrerPolicy="no-referrer"
          className="h-full w-full border-0 bg-white"
          data-testid="canvas-frame"
        />
      </div>
    </div>
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
  content,
  labeled = false,
}: {
  artifact: CanvasArtifact;
  /** What to download — the live edit buffer when editing. */
  content?: string;
  labeled?: boolean;
}) {
  const t = useT();

  function download() {
    const blob = new Blob([content ?? artifact.content], {
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
