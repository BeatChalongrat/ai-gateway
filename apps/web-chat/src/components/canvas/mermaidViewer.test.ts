// The Mermaid canvas viewer's page: controls with the app's labels, the
// keyboard and wheel handlers, focusable, and Mermaid run by the viewer (so
// it can fit the drawn diagram) — never an unescaped label or diagram. The
// behaviour itself runs in a real browser: tests/e2e/canvas-mermaid-viewer.spec.ts.
import { describe, expect, it } from "vitest";

import { buildMermaidSrcDoc } from "./canvasUtil";

describe("the Mermaid canvas viewer", () => {
  const doc = buildMermaidSrcDoc("flowchart LR\n A-->B", "http://app.test", {
    zoomIn: "ขยาย (+)",
    zoomOut: "ย่อ (−)",
    fit: 'พอดี "หน้าจอ" <0>',
    level: "ระดับการซูม",
  });

  it("has zoom in, zoom out, fit and the level, labelled in the app's language", () => {
    expect(doc).toContain('id="zoom-in" aria-label="ขยาย (+)"');
    expect(doc).toContain('id="zoom-out" aria-label="ย่อ (−)"');
    expect(doc).toContain('id="zoom-level"');
    expect(doc).toContain('aria-label="พอดี &quot;หน้าจอ&quot; &lt;0&gt;"');
  });

  it("answers the wheel, a drag, a pinch and + / − / 0 on a focusable stage", () => {
    expect(doc).toContain('<div id="stage" tabindex="0">');
    for (const s of ['addEventListener("wheel"', '"pointerdown"', '"pointermove"', 'e.key==="+"', 'e.key==="-"', 'e.key==="0"']) {
      expect(doc).toContain(s);
    }
  });

  it("runs Mermaid itself, strictly, and fits once it has drawn", () => {
    expect(doc).toContain('startOnLoad:false,securityLevel:"strict"');
    expect(doc).toContain("mermaid.run(");
    expect(doc).toMatch(/\.then\(function\(\)\{[^}]*size\(svg\);fit\(\)/);
  });

  it("keeps the diagram source as escaped text", () => {
    const evil = buildMermaidSrcDoc("graph TD\nA[<img src=x onerror=alert(1)>]", "http://app.test");
    expect(evil).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(evil).not.toContain("<img src=x");
  });
});
