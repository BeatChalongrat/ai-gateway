// Phase 6 / C1 — Canvas helpers: eligibility detection, the sandboxed
// srcdoc builder (CSP injection), and the SVG sanitizer. Pure
// functions so the security posture is unit-testable without DOM
// rendering.

import type { CanvasArtifactType } from "@/stores/canvasStore";

// ---------------------------------------------------------------------------
// Size guard — a canvas is a preview, not a file viewer. Beyond this
// many characters we refuse to render and offer download/copy instead
// (a multi-MB srcdoc can freeze the tab).
// ---------------------------------------------------------------------------
export const MAX_CANVAS_CHARS = 512 * 1024;

// ---------------------------------------------------------------------------
// Eligibility — which fenced code blocks get the "Open in Canvas"
// button. Deliberately conservative:
//   - html/htm      → static HTML preview (sandboxed iframe)
//   - svg, or an xml/unlabelled block whose content IS an <svg> root
//                   → sanitized SVG preview (same iframe sandbox)
//   - markdown/md   → ReactMarkdown preview (existing safe pipeline)
// ``mermaid`` is NOT eligible in C1 — web-chat has no mermaid renderer
// to reuse and C1 forbids writing a new one. Everything else (python,
// js, json, …) keeps the plain code block only.
// ---------------------------------------------------------------------------
export function canvasTypeForBlock(
  lang: string,
  content: string,
): CanvasArtifactType | null {
  const l = lang.trim().toLowerCase();
  if (l === "html" || l === "htm") return "html";
  if (l === "markdown" || l === "md") return "markdown";
  if (l === "svg") return "svg";
  if (l === "" || l === "xml") {
    // Unlabelled/xml fence that is actually a standalone SVG document.
    const head = content.trimStart().slice(0, 512).toLowerCase();
    if (head.startsWith("<svg") || /^<\?xml[^>]*\?>\s*<svg/.test(head)) {
      return "svg";
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Sandboxed srcdoc builder. The security model is TWO independent
// layers — either alone blocks script execution / exfiltration, both
// are always applied:
//
//  1. The <iframe> carries ``sandbox=""`` (empty allowlist): no
//     allow-scripts (JS cannot run at all), no allow-same-origin (the
//     frame is a unique opaque origin — no cookies, no parent DOM, no
//     storage), no allow-forms/popups/top-navigation.
//  2. This CSP <meta> is injected into the document itself:
//     ``default-src 'none'`` blocks EVERY network fetch (img/css/font/
//     frame/fetch → no exfiltration beacons), with only two openings:
//     data:-URI images and inline <style>. Even if a future refactor
//     weakened the sandbox attribute, content still cannot phone home.
// ---------------------------------------------------------------------------
export const CANVAS_CSP =
  "default-src 'none'; img-src data:; style-src 'unsafe-inline'";

const CSP_META = `<meta http-equiv="Content-Security-Policy" content="${CANVAS_CSP}">`;

/** Wraps/patches artifact HTML so the CSP meta is ALWAYS present and
 *  as early as possible. Full documents get it injected right after
 *  <head> (or before <body> content when no head exists); fragments
 *  get a minimal skeleton. */
export function buildHtmlSrcDoc(html: string): string {
  const headMatch = /<head[^>]*>/i.exec(html);
  if (headMatch) {
    const at = headMatch.index + headMatch[0].length;
    return html.slice(0, at) + CSP_META + html.slice(at);
  }
  const htmlTagMatch = /<html[^>]*>/i.exec(html);
  if (htmlTagMatch) {
    const at = htmlTagMatch.index + htmlTagMatch[0].length;
    return html.slice(0, at) + `<head>${CSP_META}</head>` + html.slice(at);
  }
  // Fragment — give it a proper skeleton.
  return `<!doctype html><html><head>${CSP_META}</head><body>${html}</body></html>`;
}

/** Wraps (sanitized) SVG markup in the same CSP'd skeleton so it
 *  renders inside the sandboxed iframe — model SVG is NEVER injected
 *  into the app's own DOM. */
export function buildSvgSrcDoc(svg: string): string {
  return (
    `<!doctype html><html><head>${CSP_META}` +
    `<style>html,body{margin:0;padding:8px;display:grid;place-items:center;min-height:100vh;box-sizing:border-box}svg{max-width:100%;height:auto}</style>` +
    `</head><body>${sanitizeSvg(svg)}</body></html>`
  );
}

// ---------------------------------------------------------------------------
// SVG sanitizer — defense-in-depth on top of the iframe sandbox.
// String-based (the content never touches a live DOM while being
// cleaned). Strips:
//   - <script>…</script> blocks (and stray <script …/> tags)
//   - <foreignObject> subtrees (can smuggle arbitrary HTML)
//   - on* event-handler attributes (onload, onclick, …)
//   - javascript:/vbscript: URLs in href/xlink:href/src
//   - <?xml-stylesheet?> processing instructions (external fetch)
// ---------------------------------------------------------------------------
export function sanitizeSvg(svg: string): string {
  let out = svg;
  out = out.replace(/<script\b[\s\S]*?<\/script\s*>/gi, "");
  out = out.replace(/<script\b[^>]*\/?>/gi, "");
  out = out.replace(/<foreignObject\b[\s\S]*?<\/foreignObject\s*>/gi, "");
  out = out.replace(/<foreignObject\b[^>]*\/?>/gi, "");
  out = out.replace(/<\?xml-stylesheet[\s\S]*?\?>/gi, "");
  // Event handlers: match the attribute inside tags in all three
  // quoting forms (double, single, unquoted).
  out = out.replace(/\son[a-z]+\s*=\s*"[^"]*"/gi, "");
  out = out.replace(/\son[a-z]+\s*=\s*'[^']*'/gi, "");
  out = out.replace(/\son[a-z]+\s*=\s*[^\s>]+/gi, "");
  // Script-scheme URLs → neutralized (keep the attribute shape valid).
  out = out.replace(
    /((?:xlink:)?href|src)\s*=\s*(["']?)\s*(?:javascript|vbscript):[^"'\s>]*\2/gi,
    '$1=$2#$2',
  );
  return out;
}

/** Suggested download filename per artifact type. */
export function canvasDownloadName(
  type: CanvasArtifactType,
  title?: string,
): string {
  if (title && title.trim()) return title.trim();
  switch (type) {
    case "html":
      return "artifact.html";
    case "svg":
      return "artifact.svg";
    case "markdown":
      return "artifact.md";
  }
}
