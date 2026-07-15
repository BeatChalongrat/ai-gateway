using System.Text.Json;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Standalone HTML document for ONE display-math block, typeset by the
/// bundled KaTeX (Assets/WebLibs/katex — <see cref="KatexVersion"/>; vendored
/// from the npm "katex" dist: katex.min.js + katex.min.css + woff2 fonts).
/// Same sandbox posture as artifacts: every asset loads from the
/// appassets.sran virtual host and the page carries the locked-down artifact
/// CSP (connect-src 'none' — fully offline). Pure string builder, no
/// WebView2 types, so it's unit-testable.
/// </summary>
public static class MathHtmlBuilder
{
    /// <summary>Pinned version of the vendored KaTeX bundle. Bump by
    /// re-vendoring dist/katex.min.{js,css} + dist/fonts/*.woff2 from the npm
    /// "katex" package into Assets/WebLibs/katex/.</summary>
    public const string KatexVersion = "0.16.21";

    public static string Build(string tex, bool dark)
    {
        // JSON-encode the TeX so backslashes/quotes/newlines survive the trip
        // into the inline script verbatim.
        var texJson = JsonSerializer.Serialize(tex);
        // Text colors mirror ArtifactHtmlBuilder.ThemeCss (app palette).
        var foreground = dark ? "#ECECEC" : "#0D0D0D";
        var errorColor = dark ? "#FF8A80" : "#B00020";

        return $$"""
            <!DOCTYPE html>
            <html><head><meta charset="utf-8">{{ArtifactSandbox.CspMetaTag()}}
            <link rel="stylesheet" href="{{ArtifactSandbox.LibBase}}/katex/katex.min.css">
            <script src="{{ArtifactSandbox.LibBase}}/katex/katex.min.js"></script>
            <style>
              html, body { margin: 0; padding: 0; background: transparent; color: {{foreground}}; overflow: hidden; }
              #math { padding: 2px 4px; overflow-x: auto; overflow-y: hidden; }
            </style></head><body>
            <div id="math"></div>
            <script>
              (function () {
                var el = document.getElementById('math');
                var tex = {{texJson}};
                try {
                  katex.render(tex, el, { displayMode: true, throwOnError: false, errorColor: '{{errorColor}}' });
                } catch (e) {
                  el.textContent = tex; // malformed math degrades to its source
                }
                function post() {
                  if (window.chrome && window.chrome.webview)
                    window.chrome.webview.postMessage(String(document.documentElement.scrollHeight || el.scrollHeight || 0));
                }
                post();
                if (document.fonts && document.fonts.ready)
                  document.fonts.ready.then(post); // height settles after the KaTeX fonts load
              })();
            </script>
            </body></html>
            """;
    }
}
