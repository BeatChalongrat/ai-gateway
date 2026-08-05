// Phase 6 / C1 — Canvas security helpers. These tests ARE the C1
// security contract for the pure layer: eligibility gating, CSP
// injection into every srcdoc shape, and the SVG sanitizer.

import { describe, expect, it } from "vitest";

import {
  CANVAS_CSP,
  MAX_CANVAS_CHARS,
  buildHtmlSrcDoc,
  buildSvgSrcDoc,
  canvasDownloadName,
  canvasTypeForBlock,
  sanitizeSvg,
} from "./canvasUtil";

describe("canvasTypeForBlock", () => {
  it("accepts html/htm, markdown/md, svg fences", () => {
    expect(canvasTypeForBlock("html", "<h1>x</h1>")).toBe("html");
    expect(canvasTypeForBlock("htm", "<h1>x</h1>")).toBe("html");
    expect(canvasTypeForBlock("markdown", "# hi")).toBe("markdown");
    expect(canvasTypeForBlock("md", "# hi")).toBe("markdown");
    expect(canvasTypeForBlock("svg", "<svg></svg>")).toBe("svg");
  });

  it("detects an svg document inside xml/unlabelled fences by content", () => {
    expect(canvasTypeForBlock("xml", '<svg viewBox="0 0 1 1"></svg>')).toBe("svg");
    expect(
      canvasTypeForBlock("", '<?xml version="1.0"?><svg></svg>'),
    ).toBe("svg");
    expect(canvasTypeForBlock("xml", "<note><to>x</to></note>")).toBeNull();
  });

  it("rejects everything else — including mermaid (no safe renderer in C1)", () => {
    expect(canvasTypeForBlock("python", "print(1)")).toBeNull();
    expect(canvasTypeForBlock("javascript", "alert(1)")).toBeNull();
    expect(canvasTypeForBlock("mermaid", "graph TD; A-->B")).toBeNull();
    expect(canvasTypeForBlock("json", "{}")).toBeNull();
    expect(canvasTypeForBlock("", "plain text")).toBeNull();
  });
});

describe("buildHtmlSrcDoc — CSP is always present", () => {
  const cspMeta = `content="${CANVAS_CSP}"`;

  it("injects the CSP right after <head> in a full document", () => {
    const doc = buildHtmlSrcDoc(
      "<!doctype html><html><head><title>t</title></head><body>hi</body></html>",
    );
    expect(doc).toContain(cspMeta);
    expect(doc.indexOf(cspMeta)).toBeLessThan(doc.indexOf("<title>"));
  });

  it("creates a <head> when the document has <html> but no head", () => {
    const doc = buildHtmlSrcDoc("<html><body>hi</body></html>");
    expect(doc).toContain(cspMeta);
    expect(doc.indexOf(cspMeta)).toBeLessThan(doc.indexOf("<body>"));
  });

  it("wraps fragments in a skeleton carrying the CSP", () => {
    const doc = buildHtmlSrcDoc("<h1>fragment</h1>");
    expect(doc).toContain(cspMeta);
    expect(doc).toContain("<h1>fragment</h1>");
    expect(doc.toLowerCase()).toContain("<!doctype html>");
  });

  it("CSP blocks all network egress except data: images + inline style", () => {
    // The policy string itself is part of the contract — default-src
    // 'none' means script/img/font/frame/fetch/css all fail closed.
    expect(CANVAS_CSP).toContain("default-src 'none'");
    expect(CANVAS_CSP).toContain("img-src data:");
    expect(CANVAS_CSP).toContain("style-src 'unsafe-inline'");
    expect(CANVAS_CSP).not.toContain("script-src");
    expect(CANVAS_CSP).not.toContain("connect-src");
    expect(CANVAS_CSP).not.toContain("http");
  });
});

describe("sanitizeSvg", () => {
  it("strips <script> blocks and self-closing script tags", () => {
    const dirty =
      '<svg><script>parent.document.title="pwned"</script><circle r="5"/><script src="https://evil/x.js"/></svg>';
    const clean = sanitizeSvg(dirty);
    expect(clean).not.toContain("<script");
    expect(clean).not.toContain("pwned");
    expect(clean).toContain("<circle");
  });

  it("strips foreignObject subtrees (HTML smuggling)", () => {
    const dirty =
      '<svg><foreignObject><body onload="alert(1)"><iframe src="https://evil"/></body></foreignObject><rect/></svg>';
    const clean = sanitizeSvg(dirty);
    expect(clean).not.toContain("foreignObject");
    expect(clean).not.toContain("iframe");
    expect(clean).toContain("<rect/>");
  });

  it("strips on* event handlers in every quoting form", () => {
    const dirty =
      "<svg onload=\"alert(1)\" onclick='alert(2)' onmouseover=alert(3)><circle r=\"1\" onend=\"evil()\"/></svg>";
    const clean = sanitizeSvg(dirty);
    expect(clean).not.toMatch(/\son[a-z]+\s*=/i);
    expect(clean).toContain("<svg");
    expect(clean).toContain("<circle");
  });

  it("neutralizes javascript:/vbscript: URLs in href/xlink:href", () => {
    const dirty =
      '<svg><a href="javascript:alert(1)">x</a><a xlink:href=\'vbscript:evil\'>y</a></svg>';
    const clean = sanitizeSvg(dirty);
    expect(clean.toLowerCase()).not.toContain("javascript:");
    expect(clean.toLowerCase()).not.toContain("vbscript:");
  });

  it("strips xml-stylesheet processing instructions (external fetch)", () => {
    const dirty =
      '<?xml-stylesheet href="https://evil/style.css"?><svg><rect/></svg>';
    expect(sanitizeSvg(dirty)).not.toContain("xml-stylesheet");
  });

  it("leaves benign SVG intact", () => {
    const ok =
      '<svg viewBox="0 0 10 10"><defs><linearGradient id="g"/></defs><circle cx="5" cy="5" r="4" fill="url(#g)"/><text x="1" y="9">ไทย</text></svg>';
    expect(sanitizeSvg(ok)).toBe(ok);
  });
});

describe("buildSvgSrcDoc", () => {
  it("wraps SANITIZED svg in a CSP'd skeleton", () => {
    const doc = buildSvgSrcDoc('<svg onload="evil()"><rect/></svg>');
    expect(doc).toContain(`content="${CANVAS_CSP}"`);
    expect(doc).not.toContain("onload");
    expect(doc).toContain("<rect/>");
  });
});

describe("misc", () => {
  it("size cap is sane and download names fall back per type", () => {
    expect(MAX_CANVAS_CHARS).toBeGreaterThanOrEqual(64 * 1024);
    expect(canvasDownloadName("html")).toBe("artifact.html");
    expect(canvasDownloadName("svg")).toBe("artifact.svg");
    expect(canvasDownloadName("markdown")).toBe("artifact.md");
    expect(canvasDownloadName("html", "report.html")).toBe("report.html");
  });
});
