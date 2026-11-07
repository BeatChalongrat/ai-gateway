// Phase 6 / C1 — Canvas panel component + security-posture tests.
//
// jsdom cannot execute iframe content, so "JS doesn't run" is proven
// structurally, which is exactly how the browser enforces it too:
//  - the ONLY render path for model HTML/SVG is an <iframe> whose
//    sandbox attribute is the EMPTY string (no allow-scripts, no
//    allow-same-origin) — asserted on the live DOM;
//  - the srcdoc always carries the default-src 'none' CSP — asserted
//    on the live DOM;
//  - no file in components/canvas/ uses dangerouslySetInnerHTML and
//    none contains an allow-scripts/allow-same-origin token — asserted
//    by scanning the source files themselves.

import fs from "node:fs";
import path from "node:path";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Markdown } from "@/components/chat/Markdown";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { useCanvasStore } from "@/stores/canvasStore";

import { CanvasPanel } from "./CanvasPanel";
import { CANVAS_CSP, MAX_CANVAS_CHARS } from "./canvasUtil";

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  useCanvasStore.setState({ open: false, artifact: null });
  useI18nStore.setState({ locale: "en" });
});

function openArtifact(
  type: import("@/stores/canvasStore").CanvasArtifactType,
  content: string,
  title?: string,
) {
  act(() => {
    useCanvasStore.getState().openCanvas({ type, content, title });
  });
}

describe("CanvasPanel shell", () => {
  it("opens from the store, shows type header + title, closes via button", () => {
    render(<CanvasPanel />);
    // FIX76 — docked (jsdom has no matchMedia → the ≥ lg branch): nothing
    // is in the row while closed; open, it is a complementary landmark
    // beside the thread, not a dialog over it.
    expect(screen.queryByTestId("canvas-panel")).toBeNull();

    openArtifact("html", "<h1>hi</h1>", "report.html");
    const panel = screen.getByTestId("canvas-panel");
    expect(panel.getAttribute("role")).toBe("complementary");
    expect(panel.getAttribute("data-pane-mode")).toBe("docked");
    expect(screen.getByText("report.html")).toBeInTheDocument();
    expect(screen.getByText(/HTML preview/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(dictionaries.en["canvas.close"]));
    expect(useCanvasStore.getState().open).toBe(false);
    expect(useCanvasStore.getState().artifact).toBeNull();
  });

  it("closes on Escape", () => {
    render(<CanvasPanel />);
    openArtifact("markdown", "# doc");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(useCanvasStore.getState().open).toBe(false);
  });

  it("renders the Thai locale strings", () => {
    useI18nStore.setState({ locale: "th" });
    render(<CanvasPanel />);
    openArtifact("svg", "<svg></svg>");
    expect(
      screen.getByText(new RegExp(dictionaries.th["canvas.typeSvg"])),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(dictionaries.th["canvas.close"]),
    ).toBeInTheDocument();
  });
});

describe("CanvasPanel security posture (C2 contract)", () => {
  it("HTML runs ONLY in an iframe sandboxed EXACTLY allow-scripts (opaque origin) with the exec CSP", () => {
    render(<CanvasPanel />);
    openArtifact(
      "html",
      '<h1>x</h1><script>window.parent.document.title="pwned"</script>',
    );
    const frame = screen.getByTestId("canvas-frame");
    expect(frame.tagName).toBe("IFRAME");
    // EXACTLY allow-scripts: scripts run, but with NO allow-same-origin
    // the document is an opaque origin — parent DOM/cookies/storage are
    // unreachable even for running JS. No top-nav, popups, forms, modals.
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain("connect-src 'none'");
    expect(srcdoc).toContain("default-src 'none'");
    expect(srcdoc).not.toContain("allow-same-origin");
    // The document (with its script) lives in srcdoc, not the app DOM.
    expect(document.title).not.toBe("pwned");
    expect(document.querySelector("h1")).toBeNull();
  });

  it("react + mermaid artifacts use the same allow-scripts frame, libs self-hosted only", () => {
    render(<CanvasPanel />);
    openArtifact("react", "const App = () => <b>hi</b>; export default App;");
    let frame = screen.getByTestId("canvas-frame");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("srcdoc")).toContain("/canvas-libs/babel.min.js");
    expect(frame.getAttribute("srcdoc")).not.toContain("cdnjs");

    openArtifact("mermaid", "graph TD; A-->B");
    frame = screen.getByTestId("canvas-frame");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("srcdoc")).toContain("/canvas-libs/mermaid.min.js");
    expect(frame.getAttribute("srcdoc")).toContain("connect-src 'none'");
  });

  it("SVG stays STATIC exactly as C1: sanitized + empty sandbox (no scripts at all)", () => {
    render(<CanvasPanel />);
    openArtifact(
      "svg",
      '<svg onload="alert(1)"><script>evil()</script><circle r="4"/></svg>',
    );
    const frame = screen.getByTestId("canvas-frame");
    expect(frame.getAttribute("sandbox")).toBe("");
    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain(CANVAS_CSP);
    expect(srcdoc).not.toContain("onload");
    expect(srcdoc).not.toContain("<script");
    expect(srcdoc).toContain("<circle");
    // No model SVG node reachable in the main document. Scoped to the
    // artifact's own r="4" circle so the panel's lucide icons (which
    // legitimately contain <circle>) can't mask a real leak.
    expect(document.querySelector('circle[r="4"]')).toBeNull();
  });

  it("oversized artifacts are refused with a download fallback (no frame mounted)", () => {
    render(<CanvasPanel />);
    openArtifact("html", "x".repeat(MAX_CANVAS_CHARS + 1));
    expect(screen.getByTestId("canvas-too-large")).toBeInTheDocument();
    expect(screen.queryByTestId("canvas-frame")).toBeNull();
    expect(
      screen.getByText(dictionaries.en["canvas.tooLarge"]),
    ).toBeInTheDocument();
  });

  it("empty artifact shows the empty state without crashing", () => {
    render(<CanvasPanel />);
    openArtifact("html", "   ");
    expect(
      screen.getByText(dictionaries.en["canvas.empty"]),
    ).toBeInTheDocument();
  });

  it("markdown preview reuses the existing safe pipeline (raw HTML stays inert)", () => {
    render(<CanvasPanel />);
    openArtifact("markdown", '# Title\n\n<script>evil()</script>\n\n**bold**');
    // Rendered through ReactMarkdown (no rehype-raw): heading + bold
    // appear, the script tag does not become a DOM node.
    expect(screen.getByText("Title").tagName).toBe("H1");
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(document.querySelector("script[src], body script")).toBeNull();
  });

  it("source scan: canvas components never use dangerouslySetInnerHTML or sandbox allow-tokens, and never touch browser storage", () => {
    // Comments may legitimately NAME the banned constructs while
    // documenting why they're absent — scan only real code.
    const stripComments = (src: string) =>
      src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    const scan = (label: string, src: string, banned: string[]) => {
      const code = stripComments(src);
      return banned.filter((b) => code.includes(b)).map((b) => `${label}: ${b}`);
    };

    const dir = path.join(process.cwd(), "src", "components", "canvas");
    const offenders: string[] = [];
    for (const f of fs.readdirSync(dir)) {
      if (!/\.(ts|tsx)$/.test(f) || /\.test\./.test(f)) continue;
      offenders.push(
        // C2: allow-scripts is now the (only) legitimate sandbox token;
        // every OTHER relaxation stays banned everywhere in this folder.
        ...scan(f, fs.readFileSync(path.join(dir, f), "utf8"), [
          "dangerouslySetInnerHTML",
          "allow-same-origin",
          "allow-top-navigation",
          "allow-popups",
          "allow-modals",
          "allow-forms",
          "allow-downloads",
          "localStorage",
          "sessionStorage",
        ]),
      );
    }
    offenders.push(
      ...scan(
        "canvasStore.ts",
        fs.readFileSync(
          path.join(process.cwd(), "src", "stores", "canvasStore.ts"),
          "utf8",
        ),
        ["localStorage", "sessionStorage", "persist("],
      ),
    );
    expect(offenders).toEqual([]);
  });
});

describe("C2 runtime UX (bootstrap postMessage protocol)", () => {
  function postFromArtifact(type: "ready" | "error", payload?: string) {
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { __sranCanvas: true, type, payload: payload ?? null },
        }),
      );
    });
  }

  it("shows the loading overlay until the artifact posts ready", () => {
    render(<CanvasPanel />);
    openArtifact("html", "<h1>app</h1><script>1+1</script>");
    expect(screen.getByTestId("canvas-loading")).toBeInTheDocument();
    postFromArtifact("ready");
    expect(screen.queryByTestId("canvas-loading")).toBeNull();
    expect(screen.queryByTestId("canvas-runtime-error")).toBeNull();
  });

  it("surfaces artifact script errors in a banner instead of failing silently", () => {
    render(<CanvasPanel />);
    openArtifact("html", "<script>boom(</script>");
    postFromArtifact("error", "boom is not defined [artifact:1]");
    const banner = screen.getByTestId("canvas-runtime-error");
    expect(banner).toHaveTextContent(dictionaries.en["canvas.runtimeError"]);
    expect(banner).toHaveTextContent("boom is not defined");
  });

  it("ignores messages without the canvas marker (no spoofed banners from random postMessage)", () => {
    render(<CanvasPanel />);
    openArtifact("html", "<h1>x</h1>");
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data: { type: "error", payload: "fake" } }),
      );
    });
    expect(screen.queryByTestId("canvas-runtime-error")).toBeNull();
  });

  it("flags a silent artifact as slow after the ready-timeout (frame kept alive)", () => {
    vi.useFakeTimers();
    try {
      render(<CanvasPanel />);
      openArtifact("html", "<script>for(;;){}</script>");
      act(() => {
        vi.advanceTimersByTime(12_500);
      });
      expect(screen.getByTestId("canvas-slow")).toHaveTextContent(
        dictionaries.en["canvas.slowLoad"],
      );
      expect(screen.getByTestId("canvas-frame")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders the Thai strings for the runtime states", () => {
    useI18nStore.setState({ locale: "th" });
    render(<CanvasPanel />);
    openArtifact("react", "const App = () => <b>x</b>");
    expect(
      screen.getByText(dictionaries.th["canvas.running"]),
    ).toBeInTheDocument();
    postFromArtifact("error", "x");
    expect(
      screen.getByText(dictionaries.th["canvas.runtimeError"]),
    ).toBeInTheDocument();
  });
});

describe("Open in Canvas affordance (CodeBlock)", () => {
  it("shows the button for eligible blocks and opens the panel with that content", () => {
    render(
      <>
        <Markdown>{"```html\n<h1>Doc</h1>\n```"}</Markdown>
        <CanvasPanel />
      </>,
    );
    const btn = screen.getByLabelText(dictionaries.en["canvas.open"]);
    fireEvent.click(btn);
    const st = useCanvasStore.getState();
    expect(st.open).toBe(true);
    expect(st.artifact?.type).toBe("html");
    expect(st.artifact?.content).toContain("<h1>Doc</h1>");
    expect(screen.getByTestId("canvas-frame")).toBeInTheDocument();
  });

  it("keeps the copy button alongside (never replaced)", () => {
    render(<Markdown>{"```svg\n<svg></svg>\n```"}</Markdown>);
    expect(
      screen.getByLabelText(dictionaries.en["canvas.open"]),
    ).toBeInTheDocument();
    expect(screen.getByText(dictionaries.en["code.copy"])).toBeInTheDocument();
  });

  it("does NOT show the button for non-eligible languages (python, json)", () => {
    render(
      <Markdown>{"```python\nprint(1)\n```\n\n```json\n{}\n```"}</Markdown>,
    );
    expect(
      screen.queryByLabelText(dictionaries.en["canvas.open"]),
    ).toBeNull();
  });

  it("C2 — shows the button for mermaid and jsx blocks", () => {
    render(
      <Markdown>
        {"```mermaid\ngraph TD; A-->B\n```\n\n```jsx\nconst App = () => <b>x</b>\n```"}
      </Markdown>,
    );
    expect(
      screen.getAllByLabelText(dictionaries.en["canvas.open"]),
    ).toHaveLength(2);
  });

  it("passes the fence filename through as the canvas title", () => {
    render(
      <>
        <Markdown>{"```html:landing.html\n<h1>x</h1>\n```"}</Markdown>
        <CanvasPanel />
      </>,
    );
    fireEvent.click(screen.getByLabelText(dictionaries.en["canvas.open"]));
    expect(useCanvasStore.getState().artifact?.title).toBe("landing.html");
  });
});
