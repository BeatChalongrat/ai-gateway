namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Sandbox policy for the in-app PDF preview (Phase 14 Task 2).
///
/// A PDF is UNTRUSTED input: it arrives from a chat attachment, which may have
/// come from anywhere, and PDF is a scriptable format. The posture here is
/// stricter than the artifact sandbox, because unlike an artifact there is
/// nothing a PDF legitimately needs to do:
///
///   • scripting OFF entirely (settings.IsScriptEnabled = false) — this is the
///     control that stops JavaScript embedded in the document;
///   • no web-message channel, so nothing in the frame can talk to the app;
///   • navigation locked to the ONE file being previewed. Not even an
///     "open in the system browser" escape hatch: a link inside an untrusted
///     document must not be able to launch the user's real browser, and the
///     user already has a Download button for anything they actually want.
///
/// Pure (no WebView2 types) so the policy is unit-testable.
/// </summary>
public static class PdfPreviewSandbox
{
    /// <summary>Virtual host mapped to the private preview folder. A host of
    /// its own — never the artifact host — so a PDF can never reach the
    /// bundled JS libraries.</summary>
    public const string VirtualHost = "pdfpreview.sran";

    public static string UrlFor(string fileName) => $"https://{VirtualHost}/{fileName}";

    /// <summary>
    /// True only for the exact document being previewed. Everything else —
    /// other hosts, other files under the same host, data:, about:blank#links,
    /// file://, mailto:, javascript: — is cancelled.
    ///
    /// Note the deliberate absence of an OpenExternal branch: the artifact
    /// sandbox has one because artifacts are user-authored and linking out is
    /// a feature; a PDF attachment has no such claim.
    /// </summary>
    public static bool IsAllowed(string? uri, string expectedFileName)
    {
        if (string.IsNullOrEmpty(uri) || string.IsNullOrEmpty(expectedFileName))
            return false;

        if (!Uri.TryCreate(uri, UriKind.Absolute, out var parsed))
            return false;

        if (!parsed.Scheme.Equals("https", StringComparison.OrdinalIgnoreCase))
            return false;

        if (!parsed.Host.Equals(VirtualHost, StringComparison.OrdinalIgnoreCase))
            return false;

        // AbsolutePath is already percent-decoded consistently by Uri; compare
        // the single path segment to the file we wrote.
        var path = parsed.AbsolutePath.TrimStart('/');
        return string.Equals(path, expectedFileName, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// A collision-free, traversal-free name for the temp copy. The server's
    /// filename never reaches the filesystem: only the extension is carried
    /// over, and a GUID supplies the rest.
    /// </summary>
    public static string TempFileName(Guid token) => $"{token:N}.pdf";
}
