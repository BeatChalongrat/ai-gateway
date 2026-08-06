// Phase 6 / C1+C2 — Canvas security helpers. These tests ARE the
// security contract for the pure layer: eligibility gating, the C2
// executable CSP (connect-src 'none', path-scoped script allowlist),
// CSP injection into every srcdoc shape, the SVG sanitizer, and the
// React/mermaid srcdoc builders.

import { describe, expect, it } from "vitest";

import {
  CANVAS_BOOTSTRAP,
  CANVAS_CSP,
  CANVAS_EXEC_SANDBOX,
  MAX_CANVAS_CHARS,
  buildHtmlSrcDoc,
  buildMermaidSrcDoc,
  buildReactSrcDoc,
  buildSvgSrcDoc,
  canvasDownloadName,
  canvasTypeForBlock,
  escapeScriptClose,
  execCanvasCsp,
  preprocessReactSource,
  sanitizeSvg,
} from "./canvasUtil";

const ORIGIN = "http://localhost:3002";

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

  it("C2 — accepts mermaid and jsx/tsx/react fences", () => {
    expect(canvasTypeForBlock("mermaid", "graph TD; A-->B")).toBe("mermaid");
    expect(canvasTypeForBlock("jsx", "const App = () => <b>x</b>")).toBe("react");
    expect(canvasTypeForBlock("tsx", "const App = () => <b>x</b>")).toBe("react");
    expect(canvasTypeForBlock("react", "function App(){}")).toBe("react");
  });

  it("rejects everything else (python, js, json, plain)", () => {
    expect(canvasTypeForBlock("python", "print(1)")).toBeNull();
    expect(canvasTypeForBlock("javascript", "alert(1)")).toBeNull();
    expect(canvasTypeForBlock("json", "{}")).toBeNull();
    expect(canvasTypeForBlock("", "plain text")).toBeNull();
  });
});

describe("execCanvasCsp — the C2 executable policy (security contract)", () => {
  const csp = execCanvasCsp(ORIGIN);

  it("blocks every network exfiltration channel", () => {
    expect(csp).toContain("default-src 'none'");
    // THE lock: running JS cannot fetch/XHR/WebSocket/sendBeacon out.
    expect(csp).toContain("connect-src 'none'");
    expect(csp).toContain("form-action 'none'");
    expect(csp).toContain("base-uri 'none'");
  });

  it("scripts may load ONLY inline + the app's /canvas-libs/ path — no CDN, no third party", () => {
    expect(csp).toContain(`script-src 'unsafe-inline' ${ORIGIN}/canvas-libs/`);
    expect(csp).not.toContain("cdnjs");
    expect(csp).not.toContain("unsafe-eval");
    // No scheme-wide allowances that would open arbitrary hosts.
    expect(csp).not.toMatch(/script-src[^;]*https:(\s|;)/);
    expect(csp).not.toMatch(/\*\s/);
  });

  it("images are data:/blob: only (no external beacons via <img>)", () => {
    expect(csp).toMatch(/img-src data: blob:/);
    expect(csp).not.toMatch(/img-src[^;]*http/);
  });

  it("sandbox attribute constant is exactly allow-scripts", () => {
    expect(CANVAS_EXEC_SANDBOX).toBe("allow-scripts");
  });
});

describe("buildHtmlSrcDoc — exec CSP + bootstrap always present", () => {
  const cspMeta = `content="${execCanvasCsp(ORIGIN)}"`;

  it("injects the CSP right after <head> in a full document", () => {
    const doc = buildHtmlSrcDoc(
      "<!doctype html><html><head><title>t</title></head><body>hi</body></html>",
      ORIGIN,
    );
    expect(doc).toContain(cspMeta);
    expect(doc.indexOf(cspMeta)).toBeLessThan(doc.indexOf("<title>"));
    expect(doc).toContain(CANVAS_BOOTSTRAP);
  });

  it("creates a <head> when the document has <html> but no head", () => {
    const doc = buildHtmlSrcDoc("<html><body>hi</body></html>", ORIGIN);
    expect(doc).toContain(cspMeta);
    expect(doc.indexOf(cspMeta)).toBeLessThan(doc.indexOf("<body>"));
  });

  it("wraps fragments in a skeleton carrying the CSP", () => {
    const doc = buildHtmlSrcDoc("<h1>fragment</h1>", ORIGIN);
    expect(doc).toContain(cspMeta);
    expect(doc).toContain("<h1>fragment</h1>");
    expect(doc.toLowerCase()).toContain("<!doctype html>");
  });

  it("static CSP (SVG path) is unchanged from C1 — still no scripts at all", () => {
    expect(CANVAS_CSP).toContain("default-src 'none'");
    expect(CANVAS_CSP).toContain("img-src data:");
    expect(CANVAS_CSP).not.toContain("script-src");
  });
});

describe("buildMermaidSrcDoc", () => {
  it("HTML-escapes the diagram source (mermaid payloads cannot inject tags)", () => {
    const doc = buildMermaidSrcDoc(
      'graph TD; A["<img src=x onerror=alert(1)>"]-->B',
      ORIGIN,
    );
    expect(doc).not.toContain("<img src=x");
    expect(doc).toContain("&lt;img src=x onerror=alert(1)&gt;");
  });

  it("loads mermaid ONLY from the self-hosted path, inits securityLevel strict", () => {
    const doc = buildMermaidSrcDoc("graph TD; A-->B", ORIGIN);
    expect(doc).toContain(`src="${ORIGIN}/canvas-libs/mermaid.min.js"`);
    expect(doc).toContain('securityLevel:"strict"');
    expect(doc).toContain(`content="${execCanvasCsp(ORIGIN)}"`);
    expect(doc).toContain(CANVAS_BOOTSTRAP);
    expect(doc).not.toContain("cdnjs");
  });
});

describe("buildReactSrcDoc + preprocessReactSource", () => {
  it("strips import lines and rewrites export default for the mount shim", () => {
    const src =
      'import React from "react";\nimport { useState } from "react";\nexport default function App() { return <b>x</b>; }';
    const out = preprocessReactSource(src);
    expect(out).not.toMatch(/^\s*import\s/m);
    expect(out).toContain("const __sranDefault = function App()");
  });

  it("bundles self-hosted React+Babel only, mounts into #root, escapes </script>", () => {
    const doc = buildReactSrcDoc(
      'const s = "</script><script>evil()"; const App = () => <div>{s}</div>;',
      ORIGIN,
    );
    // The user's script-close is neutralized so it cannot terminate the
    // wrapper tag; the raw breakout sequence never appears in user code.
    expect(doc).toContain('const s = "<\\/script><script>evil()"');
    for (const lib of [
      "react.production.min.js",
      "react-dom.production.min.js",
      "babel.min.js",
    ]) {
      expect(doc).toContain(`src="${ORIGIN}/canvas-libs/${lib}"`);
    }
    expect(doc).toContain('type="text/babel"');
    expect(doc).toContain('<div id="root">');
    expect(doc).toContain("createRoot");
    expect(doc).toContain(`content="${execCanvasCsp(ORIGIN)}"`);
    expect(doc).not.toContain("cdnjs");
  });

  it("escapeScriptClose neutralizes premature script termination", () => {
    expect(escapeScriptClose('const x = "</script><script>evil()";')).toBe(
      'const x = "<\\/script><script>evil()";',
    );
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
