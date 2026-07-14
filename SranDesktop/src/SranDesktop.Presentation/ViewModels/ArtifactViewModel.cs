using System.IO;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Presentation.Rendering;
using SranDesktop.Presentation.Services;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>A live-rendered artifact (react | html | svg | mermaid | code) — R9 A4.</summary>
public partial class ArtifactViewModel : ObservableObject
{
    public string Id { get; }

    [ObservableProperty] private string _title;
    [ObservableProperty] private string _type;
    [ObservableProperty] private string _sourceCode;
    [ObservableProperty] private bool _isFullscreen;
    [ObservableProperty] private int _version = 1;

    /// <summary>Optional language for type=code fences (syntax hint only).</summary>
    public string? Language { get; }

    public ArtifactViewModel(string id, string title, string type, string sourceCode, string? language = null)
    {
        Id = id;
        _title = string.IsNullOrWhiteSpace(title) ? "Artifact" : title;
        _type = type;
        _sourceCode = sourceCode;
        Language = language;
    }

    /// <summary>Full standalone HTML for the in-app WebView2 — references the
    /// bundled JS libs via the appassets.sran virtual host + carries the CSP.</summary>
    public string BuildFullHtml() =>
        ArtifactHtmlBuilder.Build(Type, SourceCode, Title, ThemeManager.IsDark, forExternalBrowser: false);

    /// <summary>HTML for "Open in browser": appassets.sran won't resolve in a
    /// real browser, so this copy falls back to the public CDN URLs — an
    /// explicit user action, opened in THEIR browser, outside the sandbox.</summary>
    public string BuildFullHtmlForExternal() =>
        ArtifactHtmlBuilder.Build(Type, SourceCode, Title, ThemeManager.IsDark, forExternalBrowser: true);

    [RelayCommand]
    private void CopySource()
    {
        try
        {
            System.Windows.Clipboard.SetText(SourceCode);
        }
        catch
        {
            // Clipboard can be locked by another process — non-fatal.
        }
    }

    [RelayCommand]
    private void ToggleFullscreen() => IsFullscreen = !IsFullscreen;

    [RelayCommand]
    private void OpenExternal()
    {
        try
        {
            var path = Path.Combine(Path.GetTempPath(), "SranDesktop",
                $"artifact-{Guid.NewGuid():N}.html");
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, BuildFullHtmlForExternal());
            System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
        }
        catch (IOException)
        {
            // Temp dir unwritable — nothing sensible to do.
        }
    }
}

/// <summary>Wraps artifact source in a theme-matched standalone HTML document (R9 A2).
/// In-app renders reference the bundled JS libs via the appassets.sran virtual
/// host and carry a locked-down CSP (Phase 6 Task 3); the external-browser copy
/// falls back to public CDNs since the virtual host doesn't resolve there.</summary>
public static class ArtifactHtmlBuilder
{
    // CDN URLs are used ONLY for the explicit "Open in browser" export.
    private const string CdnReact = "https://unpkg.com/react@18/umd/react.production.min.js";
    private const string CdnReactDom = "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js";
    private const string CdnBabel = "https://unpkg.com/@babel/standalone@7/babel.min.js";
    private const string CdnMermaid = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js";

    public static string Build(string type, string source, string title, bool dark, bool forExternalBrowser = false)
    {
        var css = ThemeCss(dark);
        var csp = forExternalBrowser ? "" : ArtifactSandbox.CspMetaTag();

        // Bundled host in-app; CDN when the user opens it in a real browser.
        var react = forExternalBrowser ? CdnReact : $"{ArtifactSandbox.LibBase}/react.production.min.js";
        var reactDom = forExternalBrowser ? CdnReactDom : $"{ArtifactSandbox.LibBase}/react-dom.production.min.js";
        var babel = forExternalBrowser ? CdnBabel : $"{ArtifactSandbox.LibBase}/babel.min.js";
        var mermaid = forExternalBrowser ? CdnMermaid : $"{ArtifactSandbox.LibBase}/mermaid.min.js";

        return type switch
        {
            "react" => $$"""
                <!DOCTYPE html>
                <html><head><meta charset="utf-8">{{csp}}<title>{{Escape(title)}}</title>
                <style>{{css}}</style>
                <script crossorigin src="{{react}}"></script>
                <script crossorigin src="{{reactDom}}"></script>
                <script src="{{babel}}"></script>
                </head><body>
                <div id="root"></div>
                <script type="text/babel" data-presets="react">
                {{StripImportsExports(source)}}
                const __App = typeof App !== 'undefined' ? App
                    : (typeof __default !== 'undefined' ? __default : null);
                if (__App) {
                    ReactDOM.createRoot(document.getElementById('root'))
                        .render(React.createElement(__App));
                } else {
                    document.getElementById('root').innerHTML =
                        '<pre style="color:var(--text-secondary)">Could not find a component to render. Export default a component (e.g. function App).</pre>';
                }
                </script>
                </body></html>
                """,

            "mermaid" => $$"""
                <!DOCTYPE html>
                <html><head><meta charset="utf-8">{{csp}}<title>{{Escape(title)}}</title>
                <style>{{css}}</style>
                <script src="{{mermaid}}"></script>
                </head><body>
                <pre class="mermaid">
                {{Escape(source)}}
                </pre>
                <script>mermaid.initialize({ startOnLoad: true, theme: '{{(dark ? "dark" : "default")}}' });</script>
                </body></html>
                """,

            "svg" => $$"""
                <!DOCTYPE html>
                <html><head><meta charset="utf-8">{{csp}}<title>{{Escape(title)}}</title>
                <style>{{css}} body{display:flex;align-items:center;justify-content:center;}</style>
                </head><body>
                {{source}}
                </body></html>
                """,

            // Verbatim full-document passthrough: inject the CSP meta into the
            // author's own <head> (or a synthesized one) rather than wrapping.
            "html" => source.Contains("<html", StringComparison.OrdinalIgnoreCase)
                ? (forExternalBrowser ? source : ArtifactSandbox.InjectCsp(source))
                : $$"""
                    <!DOCTYPE html>
                    <html><head><meta charset="utf-8">{{csp}}<title>{{Escape(title)}}</title>
                    <style>{{css}}</style></head>
                    <body>{{source}}</body></html>
                    """,

            // code / unknown → escaped <pre>
            _ => $$"""
                <!DOCTYPE html>
                <html><head><meta charset="utf-8">{{csp}}<title>{{Escape(title)}}</title>
                <style>{{css}} pre{white-space:pre-wrap;font-family:Consolas,monospace;font-size:13px;padding:12px;}</style>
                </head><body><pre>{{Escape(source)}}</pre></body></html>
                """
        };
    }

    /// <summary>Theme-matched CSS variables mirroring the app palette (R9 A2).</summary>
    private static string ThemeCss(bool dark) => dark
        ? """
          :root { --bg:#212121; --bg-secondary:#2F2F2F; --text:#ECECEC; --text-secondary:#A0A0A0; --border:#3A3A3A; color-scheme: dark; }
          html,body { margin:0; padding:12px; background:var(--bg); color:var(--text); font-family:'Segoe UI',sans-serif; }
          """
        : """
          :root { --bg:#FFFFFF; --bg-secondary:#F0F0F0; --text:#0D0D0D; --text-secondary:#5B5B5B; --border:#E5E5E5; color-scheme: light; }
          html,body { margin:0; padding:12px; background:var(--bg); color:var(--text); font-family:'Segoe UI',sans-serif; }
          """;

    /// <summary>
    /// Babel-standalone can't resolve module imports; strip them and rewrite
    /// "export default function App" style declarations to plain declarations.
    /// React hooks are re-exposed as globals.
    /// </summary>
    internal static string StripImportsExports(string source)
    {
        var lines = source.Split('\n');
        var kept = new List<string>
        {
            "const { useState, useEffect, useMemo, useCallback, useRef, useReducer, useContext } = React;"
        };
        foreach (var line in lines)
        {
            var trimmed = line.TrimStart();
            if (trimmed.StartsWith("import ", StringComparison.Ordinal))
                continue;
            if (trimmed.StartsWith("export default function", StringComparison.Ordinal))
            {
                kept.Add(line.Replace("export default function", "function"));
                continue;
            }
            if (trimmed.StartsWith("export default", StringComparison.Ordinal))
            {
                kept.Add(line.Replace("export default", "const __default ="));
                continue;
            }
            if (trimmed.StartsWith("export ", StringComparison.Ordinal))
            {
                kept.Add(line.Replace("export ", ""));
                continue;
            }
            kept.Add(line);
        }
        return string.Join('\n', kept);
    }

    private static string Escape(string text) => text
        .Replace("&", "&amp;")
        .Replace("<", "&lt;")
        .Replace(">", "&gt;");
}
