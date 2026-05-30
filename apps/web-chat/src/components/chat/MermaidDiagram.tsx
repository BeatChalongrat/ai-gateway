"use client";

// Renders a ```mermaid code block as an SVG diagram. Mounted by
// CodeBlock only once the message has COMPLETED (never mid-stream —
// partial mermaid source is invalid and would flash errors).
//
// Mermaid (~500KB+) is loaded on demand: the library is dynamic-
// imported the first time any diagram mounts and cached at module
// scope, so a chat with no diagrams never pays for it.
//
// Invalid syntax falls back to showing the raw source + the error,
// so a bad diagram never breaks the surrounding message. A toggle
// flips between the rendered diagram and its source (ChatGPT/Claude
// parity). Theme follows the app's dark/light setting.

import { useEffect, useId, useRef, useState } from "react";

import { useThemeStore } from "@/stores/themeStore";

type MermaidApi = typeof import("mermaid")["default"];

let mermaidMod: MermaidApi | null = null;
let mermaidPromise: Promise<MermaidApi> | null = null;

function loadMermaid(): Promise<MermaidApi> {
  if (mermaidMod) return Promise.resolve(mermaidMod);
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((m) => {
      mermaidMod = m.default;
      return mermaidMod;
    });
  }
  return mermaidPromise;
}

export function MermaidDiagram({ code }: { code: string }) {
  const theme = useThemeStore((s) => s.theme);
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);

  // mermaid.render needs a DOM-id-safe, unique handle.
  const rawId = useId();
  const renderId = `mmd-${rawId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  // Guards against a slow render resolving after the code/theme changed.
  const renderSeq = useRef(0);

  useEffect(() => {
    const seq = ++renderSeq.current;
    setSvg(null);
    setError(null);
    loadMermaid()
      .then(async (mermaid) => {
        mermaid.initialize({
          startOnLoad: false,
          // strict → mermaid sanitizes labels/HTML, safe for
          // assistant-generated source.
          securityLevel: "strict",
          theme: theme === "light" ? "default" : "dark",
        });
        const { svg } = await mermaid.render(renderId, code);
        if (seq === renderSeq.current) setSvg(svg);
      })
      .catch((e: unknown) => {
        if (seq === renderSeq.current) {
          const msg =
            e instanceof Error ? e.message : "Invalid diagram syntax";
          setError(msg);
        }
      });
  }, [code, theme, renderId]);

  // Error → render the source as a plain code panel + the error note,
  // so the message stays intact and the user can see what was emitted.
  if (error) {
    return (
      <div className="code-block not-prose">
        <div className="code-block-header">
          <span className="code-block-lang">Mermaid — couldn’t render</span>
        </div>
        <div className="code-block-body scrollbar-thin">
          <pre className="code-block-content">
            <code>{code}</code>
          </pre>
        </div>
        <div className="px-3 py-1.5 text-[11px] text-status-danger">{error}</div>
      </div>
    );
  }

  return (
    <div className="code-block not-prose">
      <div className="code-block-header">
        <span className="code-block-lang">Diagram</span>
        <button
          type="button"
          onClick={() => setShowSource((v) => !v)}
          className="code-block-copy focus-ring"
          aria-pressed={showSource}
        >
          {showSource ? "Diagram" : "Source"}
        </button>
      </div>
      {showSource ? (
        <div className="code-block-body scrollbar-thin">
          <pre className="code-block-content">
            <code>{code}</code>
          </pre>
        </div>
      ) : svg ? (
        <div
          className="flex justify-center overflow-x-auto p-3 [&_svg]:h-auto [&_svg]:max-w-full"
          // mermaid output is sanitized (securityLevel: strict).
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      ) : (
        <div className="px-3 py-3 text-xs text-text-muted">Rendering diagram…</div>
      )}
    </div>
  );
}
