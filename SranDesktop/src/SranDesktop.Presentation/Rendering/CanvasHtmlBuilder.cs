using System.Text.RegularExpressions;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Builds the standalone document a canvas renders (Phase 15 Task 1).
///
/// One builder per artifact type, each carrying <see cref="CanvasSandbox"/>'s
/// CSP and the bootstrap that reports readiness/errors back over postMessage —
/// so an artifact whose script throws SAYS SO instead of showing a blank pane.
///
/// Every library reference points at the bundled canvas host. The desktop has
/// shipped React / Babel / mermaid in Assets/WebLibs since Phase 6; this is
/// the same set the web's /canvas-libs/ was vendored from, which is why the
/// two clients render an artifact identically and both work offline.
/// </summary>
public static class CanvasHtmlBuilder
{
    /// <summary>
    /// Injected into every executable canvas. Reports load + uncaught errors,
    /// stamped with the render token so the host can tell a message from the
    /// CURRENT document apart from one from a document being torn down.
    /// </summary>
    private static string Bootstrap(string token) =>
        "<script>(function(){" +
        "var T=" + JsString(token) + ";" +
        "function post(t,p){try{window.chrome.webview.postMessage(JSON.stringify({" +
        $"{CanvasSandbox.MessageMarker}:true,token:T,type:t," +
        "payload:p==null?null:String(p).slice(0,500)}))}catch(e){}}" +
        "window.addEventListener('error',function(e){post('error',(e.message||'Script error')+(e.lineno?' [line '+e.lineno+']':''))});" +
        "window.addEventListener('unhandledrejection',function(e){post('error','Unhandled rejection: '+((e.reason&&e.reason.message)||e.reason||''))});" +
        "window.addEventListener('load',function(){post('ready')});" +
        "})()</script>";

    public static string Build(string type, string source, string title, bool dark, string token)
    {
        var head = CanvasSandbox.CspMetaTag() + Bootstrap(token) + $"<style>{ThemeCss(dark)}</style>";
        var src = source ?? string.Empty;

        return type switch
        {
            "react" => BuildReact(src, head),
            "mermaid" => BuildMermaid(src, head, dark),
            "svg" => BuildSvg(src, head),
            "markdown" => BuildMarkdown(src, head),
            "html" => BuildHtml(src, head),
            _ => BuildPlain(src, head),
        };
    }

    // ── html ────────────────────────────────────────────────────────────────

    /// <summary>A full document keeps its own structure — the CSP meta is
    /// injected into its head. Multiple CSP policies INTERSECT in the browser,
    /// so a document shipping its own (looser) policy can never relax ours.</summary>
    private static string BuildHtml(string source, string head) =>
        source.Contains("<html", StringComparison.OrdinalIgnoreCase)
            ? InjectIntoHead(source, head)
            : $"<!doctype html><html><head><meta charset=\"utf-8\">{head}</head><body>{source}</body></html>";

    // ── react ───────────────────────────────────────────────────────────────

    private static string BuildReact(string source, string head)
    {
        var user = EscapeScriptClose(PreprocessReactSource(source));
        const string prelude =
            "const {useState,useEffect,useMemo,useCallback,useRef,useReducer,useContext,useId,Fragment} = React;\n";
        const string mount =
            "\n;(function(){" +
            "var C=(typeof __sranDefault!=='undefined'&&__sranDefault)||(typeof App!=='undefined'&&App)||(typeof Component!=='undefined'&&Component)||null;" +
            "if(C){ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(C));}" +
            "else{throw new Error('No component found — export default a component or name it App');}" +
            "})();";

        return "<!doctype html><html><head><meta charset=\"utf-8\">" + head +
               "<style>#root{min-height:100vh}</style></head><body>" +
               "<div id=\"root\"></div>" +
               Lib("react.production.min.js") +
               Lib("react-dom.production.min.js") +
               Lib("babel.min.js") +
               $"<script type=\"text/babel\" data-presets=\"react\">{prelude}{user}{mount}</script>" +
               "</body></html>";
    }

    // ── mermaid ─────────────────────────────────────────────────────────────

    /// <summary>
    /// The diagram source goes in HTML-ESCAPED as the text of a
    /// &lt;pre class="mermaid"&gt; and mermaid renders it INSIDE the sandbox —
    /// model-authored diagram source is never injected into the app's own DOM.
    /// securityLevel "strict" additionally keeps mermaid's HTML-in-label
    /// features off, so a mermaid parser bug cannot become script execution
    /// with any more reach than the sandbox already allows (which is none).
    /// </summary>
    private static string BuildMermaid(string source, string head, bool dark) =>
        "<!doctype html><html><head><meta charset=\"utf-8\">" + head +
        "<style>body{display:grid;place-items:center;min-height:100vh;box-sizing:border-box}</style>" +
        "</head><body>" +
        $"<pre class=\"mermaid\">{Escape(source)}</pre>" +
        Lib("mermaid.min.js") +
        "<script>try{mermaid.initialize({startOnLoad:true,securityLevel:'strict',theme:'" +
        (dark ? "dark" : "default") + "'})}catch(e){setTimeout(function(){throw e})}</script>" +
        "</body></html>";

    // ── svg ─────────────────────────────────────────────────────────────────

    /// <summary>Sanitized before it is embedded — defense in depth on top of
    /// the sandbox, mirroring the web sanitizer.</summary>
    private static string BuildSvg(string source, string head) =>
        "<!doctype html><html><head><meta charset=\"utf-8\">" + head +
        "<style>body{display:grid;place-items:center;min-height:100vh;box-sizing:border-box}svg{max-width:100%;height:auto}</style>" +
        "</head><body>" + SanitizeSvg(source) + "</body></html>";

    // ── markdown / plain ────────────────────────────────────────────────────

    /// <summary>Markdown renders as escaped preformatted text inside the
    /// sandbox. It is a PREVIEW of the source, not an HTML conversion: running
    /// a markdown parser here would re-open the raw-HTML-in-markdown hole for
    /// no benefit the transcript's own renderer doesn't already provide.</summary>
    private static string BuildMarkdown(string source, string head) =>
        "<!doctype html><html><head><meta charset=\"utf-8\">" + head +
        "<style>pre{white-space:pre-wrap;word-break:break-word;font-family:'Cascadia Code',Consolas,monospace;font-size:13px}</style>" +
        "</head><body><pre>" + Escape(source) + "</pre></body></html>";

    private static string BuildPlain(string source, string head) =>
        "<!doctype html><html><head><meta charset=\"utf-8\">" + head +
        "<style>pre{white-space:pre-wrap;font-family:'Cascadia Code',Consolas,monospace;font-size:13px}</style>" +
        "</head><body><pre>" + Escape(source) + "</pre></body></html>";

    // ── helpers ─────────────────────────────────────────────────────────────

    private static string Lib(string file) => $"<script src=\"{CanvasSandbox.LibBase}/{file}\"></script>";

    private static readonly Regex HeadOpen = new("<head[^>]*>", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex HtmlOpen = new("<html[^>]*>", RegexOptions.IgnoreCase | RegexOptions.Compiled);

    private static string InjectIntoHead(string html, string content)
    {
        var head = HeadOpen.Match(html);
        if (head.Success)
            return html.Insert(head.Index + head.Length, content);
        var htmlTag = HtmlOpen.Match(html);
        if (htmlTag.Success)
            return html.Insert(htmlTag.Index + htmlTag.Length, $"<head>{content}</head>");
        return $"<!doctype html><html><head>{content}</head><body>{html}</body></html>";
    }

    /// <summary>Babel-standalone runs classic scripts: module syntax has to
    /// go, and the default export needs a name the mount shim can find.</summary>
    internal static string PreprocessReactSource(string code)
    {
        var out1 = Regex.Replace(code, @"^\s*import\s[^\n]*$", "", RegexOptions.Multiline);
        out1 = Regex.Replace(out1, @"^\s*export\s+default\s+", "const __sranDefault = ", RegexOptions.Multiline);
        out1 = Regex.Replace(out1, @"^\s*export\s+(const|let|var|function|class)\s", "$1 ", RegexOptions.Multiline);
        return out1;
    }

    /// <summary>Stops artifact code from closing the wrapper &lt;script&gt;
    /// early and escaping into HTML position.</summary>
    internal static string EscapeScriptClose(string code) =>
        Regex.Replace(code, "</script", "<\\/script", RegexOptions.IgnoreCase);

    /// <summary>
    /// String-based SVG sanitizer (the markup never touches a live DOM while
    /// being cleaned). Mirrors the web's: strips scripts, foreignObject,
    /// on* handlers, script-scheme URLs and xml-stylesheet PIs.
    /// </summary>
    internal static string SanitizeSvg(string svg)
    {
        var outSvg = svg ?? string.Empty;
        outSvg = Regex.Replace(outSvg, @"<script\b[\s\S]*?</script\s*>", "", RegexOptions.IgnoreCase);
        outSvg = Regex.Replace(outSvg, @"<script\b[^>]*/?>", "", RegexOptions.IgnoreCase);
        outSvg = Regex.Replace(outSvg, @"<foreignObject\b[\s\S]*?</foreignObject\s*>", "", RegexOptions.IgnoreCase);
        outSvg = Regex.Replace(outSvg, @"<foreignObject\b[^>]*/?>", "", RegexOptions.IgnoreCase);
        outSvg = Regex.Replace(outSvg, @"<\?xml-stylesheet[\s\S]*?\?>", "", RegexOptions.IgnoreCase);
        outSvg = Regex.Replace(outSvg, @"\son[a-z]+\s*=\s*""[^""]*""", "", RegexOptions.IgnoreCase);
        outSvg = Regex.Replace(outSvg, @"\son[a-z]+\s*=\s*'[^']*'", "", RegexOptions.IgnoreCase);
        outSvg = Regex.Replace(outSvg, @"\son[a-z]+\s*=\s*[^\s>]+", "", RegexOptions.IgnoreCase);
        outSvg = Regex.Replace(
            outSvg,
            @"((?:xlink:)?href|src)\s*=\s*([""']?)\s*(?:javascript|vbscript):[^""'\s>]*\2",
            "$1=$2#$2",
            RegexOptions.IgnoreCase);
        return outSvg;
    }

    private static string Escape(string text) => (text ?? string.Empty)
        .Replace("&", "&amp;")
        .Replace("<", "&lt;")
        .Replace(">", "&gt;");

    /// <summary>JSON-quotes a value for embedding in a script literal.</summary>
    private static string JsString(string value) =>
        System.Text.Json.JsonSerializer.Serialize(value);

    private static string ThemeCss(bool dark) => dark
        ? ":root{color-scheme:dark}html,body{margin:0;padding:12px;background:#212121;color:#ECECEC;font-family:'Segoe UI',sans-serif}"
        : ":root{color-scheme:light}html,body{margin:0;padding:12px;background:#FFFFFF;color:#0D0D0D;font-family:'Segoe UI',sans-serif}";
}
