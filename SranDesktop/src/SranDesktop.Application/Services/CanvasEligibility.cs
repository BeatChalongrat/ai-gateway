namespace SranDesktop.Application.Services;

/// <summary>
/// Which fenced code blocks get an "Open in Canvas" button, and as what
/// (Phase 15 Task 1). Mirrors the web's <c>canvasTypeForBlock</c>
/// (apps/web-chat/src/components/canvas/canvasUtil.ts) so a block that opens
/// as React on the web opens as React here — the two clients must not disagree
/// about what a block IS, or the same artifact would render differently.
///
/// Deliberately conservative: python/js/json/bash keep the plain code block.
/// A language we do not recognise is not a canvas.
/// </summary>
public static class CanvasEligibility
{
    /// <summary>
    /// A canvas is a preview, not a file viewer. Past this many characters we
    /// refuse to open one (a multi-MB document freezes the embedded browser)
    /// and the user keeps copy/download. Same cap as the web.
    /// </summary>
    public const int MaxCanvasChars = 512 * 1024;

    /// <summary>The canvas type for a fence, or null when it is not eligible.</summary>
    public static string? TypeFor(string? language, string? content)
    {
        var lang = (language ?? string.Empty).Trim().ToLowerInvariant();
        var body = content ?? string.Empty;

        switch (lang)
        {
            case "html" or "htm":
                return "html";
            case "markdown" or "md":
                return "markdown";
            case "svg":
                return "svg";
            case "mermaid":
                return "mermaid";
            case "jsx" or "tsx" or "react":
                return "react";
        }

        // An unlabelled or xml fence that is actually a standalone SVG
        // document — common when a model forgets the language tag.
        if (lang.Length == 0 || lang == "xml")
        {
            var head = body.TrimStart();
            head = head[..Math.Min(head.Length, 512)].ToLowerInvariant();
            if (head.StartsWith("<svg", StringComparison.Ordinal))
                return "svg";
            if (head.StartsWith("<?xml", StringComparison.Ordinal))
            {
                var close = head.IndexOf("?>", StringComparison.Ordinal);
                if (close >= 0 && head[(close + 2)..].TrimStart().StartsWith("<svg", StringComparison.Ordinal))
                    return "svg";
            }
        }

        return null;
    }

    /// <summary>Eligible AND within the size cap — what the button gates on.</summary>
    public static bool CanOpen(string? language, string? content) =>
        TypeFor(language, content) is not null
        && (content?.Length ?? 0) <= MaxCanvasChars;

    /// <summary>Suggested download filename per canvas type.</summary>
    public static string DownloadName(string? type, string? title)
    {
        if (!string.IsNullOrWhiteSpace(title))
            return title!.Trim();
        return type switch
        {
            "html" => "artifact.html",
            "svg" => "artifact.svg",
            "markdown" => "artifact.md",
            "mermaid" => "artifact.mmd",
            "react" => "artifact.jsx",
            _ => "artifact.txt",
        };
    }

    /// <summary>
    /// Stable identity for one artifact lineage, byte-compatible with the
    /// web's <c>artifactKeyFor</c> (djb2 over the ORIGINAL source, base36).
    /// It MUST match: the desktop and the web have to resolve the same block
    /// to the same server lineage, or editing on one client would silently
    /// start a second version history on the other.
    /// </summary>
    public static string ArtifactKeyFor(string? messageId, string? content)
    {
        var body = content ?? string.Empty;
        // djb2, kept in int32 the way JavaScript's | 0 does, then read as
        // unsigned — the exact sequence the web performs.
        var h = 5381;
        foreach (var ch in body)
            unchecked { h = (h << 5) + h + ch; }
        var unsigned = unchecked((uint)h);
        return $"{(string.IsNullOrEmpty(messageId) ? "adhoc" : messageId)}:{ToBase36(unsigned)}";
    }

    private static string ToBase36(uint value)
    {
        if (value == 0) return "0";
        const string digits = "0123456789abcdefghijklmnopqrstuvwxyz";
        var buffer = new char[13];
        var index = buffer.Length;
        while (value > 0)
        {
            buffer[--index] = digits[(int)(value % 36)];
            value /= 36;
        }
        return new string(buffer, index, buffer.Length - index);
    }
}
