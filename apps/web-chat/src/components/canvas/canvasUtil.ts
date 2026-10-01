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
//   - html/htm      → EXECUTABLE HTML artifact (C2: scripts run inside
//                     the allow-scripts sandbox)
//   - jsx/tsx/react → React artifact (transpiled by the self-hosted
//                     Babel inside the same sandbox)
//   - mermaid       → mermaid diagram (self-hosted mermaid.js inside
//                     the same sandbox — re-enabled in C2)
//   - svg, or an xml/unlabelled block whose content IS an <svg> root
//                   → sanitized STATIC SVG preview (no scripts, as C1)
//   - markdown/md   → ReactMarkdown preview (existing safe pipeline)
// Everything else (python, js, json, …) keeps the plain code block.
// ---------------------------------------------------------------------------
export function canvasTypeForBlock(
  lang: string,
  content: string,
): CanvasArtifactType | null {
  const l = lang.trim().toLowerCase();
  if (l === "html" || l === "htm") return "html";
  if (l === "markdown" || l === "md") return "markdown";
  if (l === "svg") return "svg";
  if (l === "mermaid") return "mermaid";
  if (l === "jsx" || l === "tsx" || l === "react") return "react";
  if (l === "" || l === "xml") {
    // Unlabelled/xml fence that is actually a standalone SVG document.
    const head = content.trimStart().slice(0, 512).toLowerCase();
    if (head.startsWith("<svg") || /^<\?xml[^>]*\?>\s*<svg/.test(head)) {
      return "svg";
    }
  }
  return null;
}

// FIX81 Part B — does this HTML depend on script to BE the thing it is?
// The inline thread preview runs with sandbox="" (script-less by design),
// so an html block that carries script would render as a dead copy of an
// interactive page: buttons that do nothing, layout that never runs (the
// owner's game). Detection errs toward "needs scripts": a card is never
// wrong, a dead preview always is. Matches <script> tags, inline on*
// handlers and javascript: URLs — a mention of these in ESCAPED text
// (&lt;script&gt;) does not match, and a raw mention in visible text only
// pushes the block to the card, the safe direction.
export function htmlNeedsScripts(html: string): boolean {
  return /<script\b/i.test(html) || /\son[a-z]+\s*=/i.test(html) || /(?:href|src|action)\s*=\s*["']?\s*javascript:/i.test(html);
}

// ---------------------------------------------------------------------------
// Sandbox security model (C2 — executable artifacts).
//
// The C1 model was "no scripts anywhere". C2 lets artifact JS RUN, so
// every guarantee now hangs on these invariants (pinned by tests — do
// not relax any of them):
//
//  1. iframe sandbox = "allow-scripts" and NOTHING ELSE. Critically
//     NO allow-same-origin: the document stays an OPAQUE ORIGIN, so
//     even running JS cannot touch parent DOM/cookies/localStorage —
//     window.parent.document throws, document.cookie is its own empty
//     jar, storage APIs reject. Also no allow-top-navigation, no
//     allow-popups, no allow-forms, no allow-modals.
//  2. CSP with ``connect-src 'none'`` blocks fetch/XHR/WebSocket/
//     sendBeacon — running JS cannot exfiltrate over the network.
//     ``default-src 'none'`` is the base; scripts may load ONLY from
//     the app origin's /canvas-libs/ path (the self-hosted React/
//     Babel/mermaid vendored from the desktop ArtifactSandbox — no
//     CDN, no third-party origin, works offline) plus inline (the
//     artifact's own code); styles inline-only; images data:/blob:.
//  3. Residual channels we CANNOT close from the parent (document as
//     known limitations, same posture as other artifact platforms):
//     self-navigation of the iframe to an external URL, and a busy
//     JS loop freezing the shared renderer thread.
// ---------------------------------------------------------------------------

/** Static CSP — still used for the SVG preview, which stays script-free
 *  exactly as in C1 (SVG never needs to run code). */
export const CANVAS_CSP =
  "default-src 'none'; img-src data:; style-src 'unsafe-inline'";

/** Sandbox attribute for executable artifacts. EXACTLY this token —
 *  the security tests assert no other token ever joins it. */
export const CANVAS_EXEC_SANDBOX = "allow-scripts";

/** Path (on the app origin) that serves the vendored artifact libs. */
export const CANVAS_LIBS_PATH = "/canvas-libs/";

/** Executable-artifact CSP. ``origin`` is the app's own origin at
 *  runtime (window.location.origin) — 'self' cannot be used because a
 *  srcdoc sandbox document has an opaque origin, so 'self' matches
 *  nothing; the explicit origin + path allowlists ONLY our four lib
 *  files. connect-src 'none' is the exfiltration lock. */
export function execCanvasCsp(origin: string): string {
  const libs = origin ? `${origin}${CANVAS_LIBS_PATH}` : "";
  return (
    "default-src 'none'; " +
    `script-src 'unsafe-inline'${libs ? " " + libs : ""}; ` +
    "style-src 'unsafe-inline'; " +
    "img-src data: blob:; " +
    "font-src data:; " +
    "connect-src 'none'; " +
    "form-action 'none'; " +
    "base-uri 'none'"
  );
}

const cspMetaFor = (csp: string) =>
  `<meta http-equiv="Content-Security-Policy" content="${csp}">`;

const CSP_META = cspMetaFor(CANVAS_CSP);

/** Marker field on every postMessage the bootstrap emits, so the panel
 *  can distinguish artifact runtime messages from unrelated ones. */
export const CANVAS_MSG_MARKER = "__sranCanvas";

/** Bootstrap injected into every EXECUTABLE srcdoc: reports readiness
 *  and uncaught errors to the parent panel via postMessage (the only
 *  channel an opaque-origin frame has — and it carries no secrets in
 *  either direction). */
export const CANVAS_BOOTSTRAP =
  "<script>(function(){" +
  `function post(t,p){try{parent.postMessage({${CANVAS_MSG_MARKER}:true,type:t,payload:p==null?null:String(p).slice(0,500)},"*")}catch(e){}}` +
  'window.addEventListener("error",function(e){post("error",(e.message||"Script error")+(e.filename?" ["+e.filename.split("/").pop()+":"+e.lineno+"]":""))});' +
  'window.addEventListener("unhandledrejection",function(e){post("error","Unhandled rejection: "+((e.reason&&e.reason.message)||e.reason||""))});' +
  'window.addEventListener("load",function(){post("ready")});' +
  "})()</script>";

/** Injects markup right after <head> (synthesizing one when absent) —
 *  shared by every srcdoc builder so the CSP always lands before any
 *  artifact content. */
function injectIntoHead(html: string, headContent: string): string {
  const headMatch = /<head[^>]*>/i.exec(html);
  if (headMatch) {
    const at = headMatch.index + headMatch[0].length;
    return html.slice(0, at) + headContent + html.slice(at);
  }
  const htmlTagMatch = /<html[^>]*>/i.exec(html);
  if (htmlTagMatch) {
    const at = htmlTagMatch.index + htmlTagMatch[0].length;
    return html.slice(0, at) + `<head>${headContent}</head>` + html.slice(at);
  }
  return `<!doctype html><html><head>${headContent}</head><body>${html}</body></html>`;
}

/** C2 — EXECUTABLE HTML srcdoc: exec CSP + bootstrap, scripts allowed
 *  to run under the sandbox invariants above. */
export function buildHtmlSrcDoc(html: string, origin = defaultOrigin()): string {
  return injectIntoHead(html, cspMetaFor(execCanvasCsp(origin)) + CANVAS_BOOTSTRAP);
}

/** HTML-escape for embedding artifact source as TEXT (mermaid <pre>). */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Neutralizes ``</script`` inside embedded artifact code so it cannot
 *  terminate the wrapper <script> tag early. Valid inside JS string
 *  literals; in raw code position the sequence only ever appears when
 *  trying to break out — which this prevents. */
export function escapeScriptClose(code: string): string {
  return code.replace(/<\/script/gi, "<\\/script");
}

/** C2 — mermaid srcdoc: the diagram source goes in HTML-ESCAPED as the
 *  text of a <pre class="mermaid">; the self-hosted mermaid.min.js
 *  renders it inside the same opaque-origin sandbox, so even a mermaid
 *  parser XSS can never reach the app (no parent access, no network).
 *  securityLevel "strict" additionally keeps mermaid's own HTML-in-
 *  label features off. */
export function buildMermaidSrcDoc(
  code: string,
  origin = defaultOrigin(),
  labels: MermaidViewerLabels = DEFAULT_MERMAID_LABELS,
): string {
  return (
    "<!doctype html><html><head>" +
    cspMetaFor(execCanvasCsp(origin)) +
    CANVAS_BOOTSTRAP +
    `<style>${MERMAID_VIEWER_CSS}</style>` +
    "</head><body>" +
    '<div id="stage" tabindex="0"><div id="view">' +
    `<pre class="mermaid">${escapeHtml(code)}</pre>` +
    "</div></div>" +
    mermaidViewerBar(labels) +
    `<script src="${origin}${CANVAS_LIBS_PATH}mermaid.min.js"></script>` +
    `<script>${escapeScriptClose(MERMAID_VIEWER_JS)}</script>` +
    "</body></html>"
  );
}

/** The viewer's control labels, in the app's language (the frame has no i18n of its own). */
export interface MermaidViewerLabels {
  zoomIn: string;
  zoomOut: string;
  fit: string;
  level: string;
}

export const DEFAULT_MERMAID_LABELS: MermaidViewerLabels = {
  zoomIn: "Zoom in (+)",
  zoomOut: "Zoom out (−)",
  fit: "Fit to view (0)",
  level: "Zoom level",
};

// The Mermaid canvas viewer (chat app). A diagram of any real size was
// rendered small and frozen: no zoom, no panning, no fit. Inside the same
// sandboxed frame (no network, no parent access), the diagram now sits in a
// viewport that:
//   * fits the whole diagram to the pane when it opens (and when the pane is
//     resized, until the person zooms or pans themselves);
//   * zooms with the toolbar's + / − buttons, the mouse wheel or a trackpad
//     pinch (around the pointer), and a two-finger pinch on a touch screen;
//   * pans by dragging; the fit button (or 0) returns to the whole diagram;
//   * shows the current zoom level;
//   * answers + / − / 0 on the keyboard while the canvas has focus.
// The same page is what the desktop app shows, so both behave the same.
const MERMAID_VIEWER_CSS =
  "html,body{margin:0;height:100%;overflow:hidden;background:#fff}" +
  "#stage{position:fixed;inset:0;overflow:hidden;touch-action:none;cursor:grab;outline:none}" +
  "#stage.dragging{cursor:grabbing}" +
  "#stage:focus-visible{box-shadow:inset 0 0 0 2px #2563eb}" +
  "#view{position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform}" +
  "#view pre.mermaid{margin:0;background:transparent}" +
  "#bar{position:fixed;right:10px;bottom:10px;display:flex;align-items:center;gap:2px;padding:3px;" +
  "background:rgba(255,255,255,.96);border:1px solid #d4d4d8;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.12);" +
  "font:12px system-ui,-apple-system,Segoe UI,sans-serif;color:#27272a;user-select:none}" +
  "#bar button{width:28px;height:28px;border:0;border-radius:6px;background:transparent;color:inherit;font:inherit;" +
  "font-size:16px;line-height:1;cursor:pointer;display:grid;place-items:center}" +
  "#bar button:hover{background:#f4f4f5}#bar button:focus-visible{outline:2px solid #2563eb}" +
  "#zoom-level{min-width:44px;text-align:center;font-variant-numeric:tabular-nums}";

function mermaidViewerBar(l: MermaidViewerLabels): string {
  const b = (id: string, label: string, glyph: string) =>
    `<button type="button" id="${id}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">${glyph}</button>`;
  return (
    `<div id="bar" role="toolbar" aria-label="${escapeHtml(l.level)}">` +
    b("zoom-out", l.zoomOut, "&minus;") +
    `<span id="zoom-level" aria-live="polite" aria-label="${escapeHtml(l.level)}">100%</span>` +
    b("zoom-in", l.zoomIn, "+") +
    b("zoom-fit", l.fit, "&#x26F6;") +
    "</div>"
  );
}

const MERMAID_VIEWER_JS = `(function(){
var stage=document.getElementById("stage"),view=document.getElementById("view"),level=document.getElementById("zoom-level");
var s=1,x=0,y=0,MIN=0.05,MAX=8,PAD=24,auto=true,pts={},pinch=null;
function post(v){try{parent.postMessage({${CANVAS_MSG_MARKER}:true,type:"zoom",payload:String(v)},"*")}catch(e){}}
function apply(){view.style.transform="translate("+x+"px,"+y+"px) scale("+s+")";var p=Math.round(s*100);level.textContent=p+"%";stage.setAttribute("data-zoom",String(p));post(p)}
function box(){var svg=view.querySelector("svg");if(!svg)return null;return{w:view.offsetWidth,h:view.offsetHeight}}
function fit(){var z=box();if(!z||!z.w||!z.h)return;var W=Math.max(1,stage.clientWidth-2*PAD),H=Math.max(1,stage.clientHeight-2*PAD);
s=Math.max(MIN,Math.min(MAX,Math.min(W/z.w,H/z.h,2)));x=(stage.clientWidth-z.w*s)/2;y=(stage.clientHeight-z.h*s)/2;auto=true;apply()}
function zoomAt(f,cx,cy){var n=Math.max(MIN,Math.min(MAX,s*f));if(n===s)return;x=cx-(cx-x)*(n/s);y=cy-(cy-y)*(n/s);s=n;auto=false;apply()}
function centre(f){zoomAt(f,stage.clientWidth/2,stage.clientHeight/2)}
stage.addEventListener("wheel",function(e){e.preventDefault();var r=stage.getBoundingClientRect();
var d=e.deltaMode===1?e.deltaY*16:e.deltaY;zoomAt(Math.exp(-d*(e.ctrlKey?0.01:0.002)),e.clientX-r.left,e.clientY-r.top)},{passive:false});
stage.addEventListener("pointerdown",function(e){stage.focus({preventScroll:true});try{stage.setPointerCapture(e.pointerId)}catch(_){}
pts[e.pointerId]={x:e.clientX,y:e.clientY};stage.classList.add("dragging")});
stage.addEventListener("pointermove",function(e){var prev=pts[e.pointerId];if(!prev)return;pts[e.pointerId]={x:e.clientX,y:e.clientY};var ids=Object.keys(pts);
if(ids.length===1){x+=e.clientX-prev.x;y+=e.clientY-prev.y;auto=false;apply()}
else if(ids.length===2){var a=pts[ids[0]],b=pts[ids[1]],d=Math.hypot(a.x-b.x,a.y-b.y),r=stage.getBoundingClientRect();
if(pinch)zoomAt(d/pinch,(a.x+b.x)/2-r.left,(a.y+b.y)/2-r.top);pinch=d}});
function up(e){delete pts[e.pointerId];if(Object.keys(pts).length<2)pinch=null;if(!Object.keys(pts).length)stage.classList.remove("dragging")}
stage.addEventListener("pointerup",up);stage.addEventListener("pointercancel",up);
document.addEventListener("keydown",function(e){if(e.ctrlKey||e.metaKey||e.altKey)return;
if(e.key==="+"||e.key==="="){centre(1.25);e.preventDefault()}else if(e.key==="-"||e.key==="_"){centre(0.8);e.preventDefault()}else if(e.key==="0"){fit();e.preventDefault()}});
document.getElementById("zoom-in").addEventListener("click",function(){centre(1.25)});
document.getElementById("zoom-out").addEventListener("click",function(){centre(0.8)});
document.getElementById("zoom-fit").addEventListener("click",fit);
window.addEventListener("resize",function(){if(auto)fit()});
function size(svg){var vb=svg.viewBox&&svg.viewBox.baseVal;var w=vb&&vb.width,h=vb&&vb.height;
if(!w||!h){var r=svg.getBBox();w=r.width;h=r.height}svg.style.maxWidth="none";svg.setAttribute("width",String(w));svg.setAttribute("height",String(h))}
try{mermaid.initialize({startOnLoad:false,securityLevel:"strict"});
mermaid.run({querySelector:"#view .mermaid"}).then(function(){var svg=view.querySelector("svg");if(svg)size(svg);fit()},function(e){setTimeout(function(){throw e})})}
catch(e){setTimeout(function(){throw e})}
})();`;

/** Strips module syntax the in-iframe classic-script world can't use:
 *  ``import`` lines go away (React/ReactDOM/hooks are provided as
 *  globals), ``export default`` becomes an assignment the mount shim
 *  can find. Exported so tests can pin the transforms. */
export function preprocessReactSource(code: string): string {
  let out = code.replace(/^\s*import\s[^\n]*$/gm, "");
  out = out.replace(/^\s*export\s+default\s+/m, "const __sranDefault = ");
  out = out.replace(/^\s*export\s+(const|let|var|function|class)\s/gm, "$1 ");
  return out;
}

/** C2 — React/JSX srcdoc: self-hosted React 18 UMD + Babel standalone
 *  transpile the artifact inside the sandbox. The mount shim renders
 *  the default export (or a component named App/Component) into #root. */
export function buildReactSrcDoc(code: string, origin = defaultOrigin()): string {
  const lib = (f: string) => `<script src="${origin}${CANVAS_LIBS_PATH}${f}"></script>`;
  const user = escapeScriptClose(preprocessReactSource(code));
  const prelude =
    "const {useState,useEffect,useMemo,useCallback,useRef,useReducer,useContext,useId,Fragment} = React;\n";
  const mountShim =
    "\n;(function(){" +
    'var C=(typeof __sranDefault!=="undefined"&&__sranDefault)||(typeof App!=="undefined"&&App)||(typeof Component!=="undefined"&&Component)||null;' +
    'if(C){ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(C));}' +
    'else{throw new Error("No component found — export default a component or name it App");}' +
    "})();";
  return (
    "<!doctype html><html><head>" +
    cspMetaFor(execCanvasCsp(origin)) +
    CANVAS_BOOTSTRAP +
    "<style>html,body{margin:0;min-height:100vh;background:#fff}#root{min-height:100vh}</style>" +
    "</head><body>" +
    '<div id="root"></div>' +
    lib("react.production.min.js") +
    lib("react-dom.production.min.js") +
    lib("babel.min.js") +
    `<script type="text/babel" data-presets="react">${prelude}${user}${mountShim}</script>` +
    "</body></html>"
  );
}

function defaultOrigin(): string {
  return typeof window !== "undefined" && window.location
    ? window.location.origin
    : "";
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
    case "mermaid":
      return "artifact.mmd";
    case "react":
      return "artifact.jsx";
  }
}
