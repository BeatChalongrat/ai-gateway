using System.Text.Json;

namespace SranDesktop.Presentation.Rendering;

/// <summary>
/// Sandbox policy for the Canvas surface (Phase 15).
///
/// Canvas runs MODEL-AUTHORED JavaScript, so this is the strictest posture in
/// the app after the PDF preview. The guarantees, all pinned by tests:
///
///  1. NO NATIVE BRIDGE. <c>AddHostObjectToScript</c> is never called for a
///     canvas WebView2 — there is no object graph for artifact JS to reach
///     into. The ONLY channel is postMessage, and it is one-way in practice
///     (the artifact reports ready/error; nothing sensitive goes back).
///  2. Every inbound message must carry <see cref="MessageMarker"/> AND the
///     per-render <see cref="CanvasRenderToken"/>. The token is minted fresh
///     for each render, so a message from a stale document (a previous
///     artifact still tearing down) cannot be mistaken for the current one.
///  3. CSP: <c>default-src 'none'</c> with <c>connect-src 'none'</c> — the
///     exfiltration lock. Running JS cannot fetch/XHR/WebSocket/sendBeacon
///     anywhere, so it cannot ship the user's data out. Scripts load ONLY
///     from the bundled canvas host; there is no CDN.
///  4. Its OWN virtual host, distinct from the artifact/KaTeX host
///     (<c>appassets.sran</c>) and the PDF host (<c>pdfpreview.sran</c>), so a
///     canvas document cannot reach another surface's assets, and a future
///     mapping change on one host cannot silently widen another.
///  5. Navigation is locked to the canvas host. Unlike the R9 artifact panel
///     there is NO "open in browser" escape: that panel renders a document the
///     user asked for by name, whereas a canvas is arbitrary model output.
/// </summary>
public static class CanvasSandbox
{
    /// <summary>Canvas-only virtual host serving the bundled JS libraries.
    /// Deliberately different from ArtifactSandbox.VirtualHost and
    /// PdfPreviewSandbox.VirtualHost.</summary>
    public const string VirtualHost = "canvas.sran";

    public const string LibBase = "https://canvas.sran";

    /// <summary>
    /// Host serving the artifact DOCUMENT itself, from a private per-window
    /// staging folder.
    ///
    /// Why a real host instead of NavigateToString: that API navigates to a
    /// <c>data:</c> URI, and this sandbox denies <c>data:</c> — so the canvas
    /// cancelled its own document and rendered an empty pane (the Phase 15
    /// bug). Serving the document over https keeps the navigation lock
    /// absolutely strict (data: stays denied for everyone, including us) and
    /// gives the document a REAL origin, which is what makes the CSP's
    /// <c>script-src https://canvas.sran</c> allowance meaningful for the
    /// React/Babel/mermaid bundles.
    ///
    /// Separate from the libs host so the document's own origin cannot read
    /// the library folder as same-origin.
    /// </summary>
    public const string DocumentHost = "canvasdoc.sran";

    public const string DocumentBase = "https://canvasdoc.sran";

    /// <summary>URL for one staged document. The name changes per render, so
    /// a re-render can never be served from cache.</summary>
    public static string DocumentUrl(string fileName) => $"{DocumentBase}/{fileName}";

    /// <summary>
    /// Executable-canvas CSP. Matches the web's <c>execCanvasCsp</c> in every
    /// security-relevant clause:
    ///
    ///   default-src 'none'  — nothing loads unless named below
    ///   script-src          — the artifact's own inline code + the bundled
    ///                         libs host. No CDN, no remote origin.
    ///                         'unsafe-eval' IS present and is required: Babel
    ///                         standalone compiles JSX at runtime, and mermaid
    ///                         evaluates its own generated code. It is
    ///                         confined to a document that can reach nothing
    ///                         (connect-src 'none', no bridge), so it grants
    ///                         no capability the inline script did not have.
    ///   connect-src 'none'  — THE exfiltration lock. Do not relax.
    ///   form-action 'none'  — no navigating away by submitting a form.
    ///   base-uri 'none'     — no rewriting relative URLs out of the sandbox.
    /// </summary>
    public const string ContentSecurityPolicy =
        "default-src 'none'; " +
        "script-src 'unsafe-inline' 'unsafe-eval' https://canvas.sran; " +
        "style-src 'unsafe-inline'; " +
        "img-src data: blob:; " +
        "font-src data: https://canvas.sran; " +
        "connect-src 'none'; " +
        "form-action 'none'; " +
        "base-uri 'none'";

    public static string CspMetaTag() =>
        $"<meta http-equiv=\"Content-Security-Policy\" content=\"{ContentSecurityPolicy}\">";

    /// <summary>Marker field on every message the canvas bootstrap posts.</summary>
    public const string MessageMarker = "__sranCanvas";

    /// <summary>
    /// Navigation policy: ONLY the two canvas hosts over https — the staged
    /// document and the bundled libraries — plus WebView2's own about: blank
    /// state. Everything else is denied outright: other hosts, http, file:,
    /// javascript:, and data: (which is why the document is served over https
    /// rather than through NavigateToString).
    ///
    /// There is no OpenExternal branch by design (see the class remarks).
    /// </summary>
    public static bool IsNavigationAllowed(string? uri)
    {
        if (string.IsNullOrEmpty(uri))
            return true; // WebView2's initial empty state

        if (uri.StartsWith("about:", StringComparison.OrdinalIgnoreCase))
            return true;

        if (!Uri.TryCreate(uri, UriKind.Absolute, out var parsed))
            return false;

        if (!parsed.Scheme.Equals("https", StringComparison.OrdinalIgnoreCase))
            return false;

        return parsed.Host.Equals(VirtualHost, StringComparison.OrdinalIgnoreCase)
               || parsed.Host.Equals(DocumentHost, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// Validates a postMessage payload from the canvas document. Returns the
    /// message type ("ready" / "error") only when the envelope carries our
    /// marker AND the token minted for the CURRENT render; anything else —
    /// unmarked JSON, a wrong or missing token, a non-object, malformed JSON —
    /// is rejected.
    /// </summary>
    public static bool TryReadMessage(
        string? json, string expectedToken, out string type, out string payload)
    {
        type = string.Empty;
        payload = string.Empty;
        if (string.IsNullOrEmpty(json) || string.IsNullOrEmpty(expectedToken))
            return false;

        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return false;

            if (!doc.RootElement.TryGetProperty(MessageMarker, out var marker)
                || marker.ValueKind != JsonValueKind.True)
                return false;

            if (!doc.RootElement.TryGetProperty("token", out var token)
                || token.ValueKind != JsonValueKind.String
                || !string.Equals(token.GetString(), expectedToken, StringComparison.Ordinal))
                return false;

            if (!doc.RootElement.TryGetProperty("type", out var kind)
                || kind.ValueKind != JsonValueKind.String)
                return false;

            type = kind.GetString() ?? string.Empty;
            if (type is not ("ready" or "error"))
                return false;

            if (doc.RootElement.TryGetProperty("payload", out var body)
                && body.ValueKind == JsonValueKind.String)
            {
                payload = body.GetString() ?? string.Empty;
                // The document controls this string; cap it so a hostile
                // artifact cannot flood the UI with a megabyte "error".
                if (payload.Length > 500)
                    payload = payload[..500];
            }
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    /// <summary>A fresh, unguessable token per render.</summary>
    public static string NewRenderToken() => Guid.NewGuid().ToString("N");
}
