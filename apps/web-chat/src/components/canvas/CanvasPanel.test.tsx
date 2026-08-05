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
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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
  type: "html" | "svg" | "markdown",
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
    const panel = screen.getByTestId("canvas-panel");
    expect(panel.className).toContain("translate-x-full"); // hidden

    openArtifact("html", "<h1>hi</h1>", "report.html");
    expect(panel.className).toContain("translate-x-0");
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

describe("CanvasPanel security posture (C1 contract)", () => {
  it("HTML renders ONLY through an iframe with an empty sandbox and the CSP in srcdoc", () => {
    render(<CanvasPanel />);
    openArtifact(
      "html",
      '<h1>x</h1><script>window.parent.document.title="pwned"</script>',
    );
    const frame = screen.getByTestId("canvas-frame");
    expect(frame.tagName).toBe("IFRAME");
    // sandbox present AND empty: no allow-scripts, no allow-same-origin.
    expect(frame.getAttribute("sandbox")).toBe("");
    const srcdoc = frame.getAttribute("srcdoc") ?? "";
    expect(srcdoc).toContain(CANVAS_CSP);
    // The document (with its script) lives in srcdoc, not the app DOM.
    expect(document.title).not.toBe("pwned");
    expect(document.querySelector("h1")).toBeNull();
  });

  it("SVG is sanitized AND confined to the same sandboxed iframe — never the app DOM", () => {
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
    // No model SVG node reachable in the main document.
    expect(document.querySelector("svg circle")).toBeNull();
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
        ...scan(f, fs.readFileSync(path.join(dir, f), "utf8"), [
          "dangerouslySetInnerHTML",
          "allow-scripts",
          "allow-same-origin",
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

  it("does NOT show the button for non-eligible languages (python, mermaid)", () => {
    render(
      <Markdown>
        {"```python\nprint(1)\n```\n\n```mermaid\ngraph TD; A-->B\n```"}
      </Markdown>,
    );
    expect(
      screen.queryByLabelText(dictionaries.en["canvas.open"]),
    ).toBeNull();
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
