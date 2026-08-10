using System.IO;
using System.Text;
using Microsoft.Web.WebView2.Core;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// The ONE way a canvas document gets into a WebView2 — used by the Canvas
/// window and by the render test, so a passing test proves what the user sees.
/// (The Phase 15 suite tested the security posture only, and a canvas that
/// rendered nothing shipped green.)
///
/// The document is STAGED TO DISK and served over https from
/// <see cref="CanvasSandbox.DocumentHost"/> rather than handed to
/// <c>NavigateToString</c>. That API navigates to a <c>data:</c> URI, which
/// this sandbox denies — the canvas was cancelling its own document. Serving
/// it over the virtual host means:
///
///   • the navigation lock stays absolutely strict (data: denied for everyone);
///   • the document gets a real origin, so the CSP's script-src allowance for
///     the bundled React/Babel/mermaid actually applies to it;
///   • each render writes a NEW filename, so a re-render after an edit can
///     never be served from the browser cache.
/// </summary>
public sealed class CanvasDocumentLoader : IDisposable
{
    private readonly string _folder;
    private string? _currentFile;

    /// <summary>Creates a private staging folder for one canvas window.</summary>
    public CanvasDocumentLoader()
    {
        _folder = Path.Combine(
            Path.GetTempPath(), "SranDesktop", "canvas", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_folder);
    }

    /// <summary>The folder to map to <see cref="CanvasSandbox.DocumentHost"/>.
    /// It holds nothing but the document currently being shown.</summary>
    public string Folder => _folder;

    /// <summary>
    /// Writes the document and navigates to it. Returns the URL used, so the
    /// caller can assert on it.
    /// </summary>
    public string Load(CoreWebView2 core, string html, string renderToken)
    {
        var fileName = $"{renderToken}.html";
        var path = Path.Combine(_folder, fileName);
        // UTF-8 WITHOUT a BOM: a BOM ahead of <!DOCTYPE> pushes the browser
        // into quirks mode, which silently changes how the artifact lays out.
        File.WriteAllText(path, html, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));

        // Only ever one document on disk — the previous render's copy goes as
        // soon as its replacement exists.
        if (_currentFile is { } previous && !string.Equals(previous, fileName, StringComparison.Ordinal))
            TryDelete(Path.Combine(_folder, previous));
        _currentFile = fileName;

        var url = CanvasSandbox.DocumentUrl(fileName);
        core.Navigate(url);
        return url;
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_folder))
                Directory.Delete(_folder, recursive: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Still held by the browser process — temp cleanup, not critical.
            System.Diagnostics.Debug.WriteLine($"Canvas staging cleanup skipped: {ex.Message}");
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // The browser may still hold the previous document; it goes with
            // the folder on Dispose.
            System.Diagnostics.Debug.WriteLine($"Canvas stale document kept: {ex.Message}");
        }
    }
}
