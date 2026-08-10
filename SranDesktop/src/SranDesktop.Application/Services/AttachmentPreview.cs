namespace SranDesktop.Application.Services;

/// <summary>How an attachment is shown in the in-app preview (Phase 14 Task 2).</summary>
public enum AttachmentPreviewKind
{
    /// <summary>Decoded bitmap in an image viewer.</summary>
    Image,

    /// <summary>Rendered as a document in the locked-down WebView2.</summary>
    Pdf,

    /// <summary>Shown as the text chat-edge extracted from it
    /// (<c>GET /files/{id}/text</c>) — Word/Excel/PowerPoint and plain text.</summary>
    ExtractedText,

    /// <summary>Nothing sensible to show: archives, installers, unknown binary.
    /// The preview says so plainly instead of rendering an empty pane.</summary>
    Unsupported,
}

/// <summary>
/// Decides which preview an attachment gets. Pure and content-type-first with
/// an extension fallback, because a chat-edge upload can arrive as
/// <c>application/octet-stream</c> when the browser or the OS failed to sniff
/// a type — in that case the filename is the only signal there is.
///
/// The default is <see cref="AttachmentPreviewKind.Unsupported"/> on purpose:
/// guessing wrong sends an executable into a document viewer, while guessing
/// "unsupported" only costs the user one click on Download.
/// </summary>
public static class AttachmentPreviewRouter
{
    /// <summary>Formats chat-edge's extractor handles (see its /files/{id}/text
    /// endpoint): Office documents plus the plain-text family.</summary>
    private static readonly HashSet<string> TextExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
        ".log", ".rtf", ".html", ".htm",
    };

    private static readonly HashSet<string> ImageExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    };

    /// <summary>Never previewed: nothing here has a document view, and several
    /// are executable. Listed explicitly so the check is auditable.</summary>
    private static readonly HashSet<string> BlockedExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso",
        ".exe", ".msi", ".dll", ".bat", ".cmd", ".ps1", ".sh", ".com", ".scr",
    };

    public static AttachmentPreviewKind Resolve(string? contentType, string? filename)
    {
        var extension = SafeExtension(filename);

        // An archive or executable is never previewed, whatever the server
        // labelled it — a mislabelled content type must not open a viewer.
        if (BlockedExtensions.Contains(extension))
            return AttachmentPreviewKind.Unsupported;

        var type = (contentType ?? string.Empty).Trim();
        // "text/plain; charset=utf-8" — the parameters are not part of the type.
        var semicolon = type.IndexOf(';');
        if (semicolon >= 0)
            type = type[..semicolon];
        type = type.Trim();

        if (type.StartsWith("image/", StringComparison.OrdinalIgnoreCase))
            return AttachmentPreviewKind.Image;
        if (type.Equals("application/pdf", StringComparison.OrdinalIgnoreCase))
            return AttachmentPreviewKind.Pdf;
        if (type.StartsWith("text/", StringComparison.OrdinalIgnoreCase)
            || IsOfficeType(type))
            return AttachmentPreviewKind.ExtractedText;

        // Content type absent or useless (octet-stream) — fall back to the name.
        if (extension == ".pdf")
            return AttachmentPreviewKind.Pdf;
        if (ImageExtensions.Contains(extension))
            return AttachmentPreviewKind.Image;
        if (TextExtensions.Contains(extension))
            return AttachmentPreviewKind.ExtractedText;

        return AttachmentPreviewKind.Unsupported;
    }

    private static bool IsOfficeType(string type) =>
        type.StartsWith("application/vnd.openxmlformats-officedocument.", StringComparison.OrdinalIgnoreCase)
        || type.StartsWith("application/vnd.ms-", StringComparison.OrdinalIgnoreCase)
        || type.Equals("application/msword", StringComparison.OrdinalIgnoreCase)
        || type.Equals("application/rtf", StringComparison.OrdinalIgnoreCase)
        || type.Equals("application/json", StringComparison.OrdinalIgnoreCase)
        || type.Equals("application/xml", StringComparison.OrdinalIgnoreCase);

    /// <summary>Extension of a filename, tolerating a null/odd name. Never
    /// throws — the filename comes from the server.</summary>
    private static string SafeExtension(string? filename)
    {
        if (string.IsNullOrWhiteSpace(filename))
            return string.Empty;
        try
        {
            return Path.GetExtension(filename) ?? string.Empty;
        }
        catch (ArgumentException)
        {
            return string.Empty;
        }
    }
}

/// <summary>
/// What the extraction endpoint's status means for the preview pane. chat-edge
/// extracts asynchronously, so "no text yet" is usually "still working", and
/// showing an empty box there would read as "this document is blank".
/// </summary>
public enum ExtractedTextState
{
    Ready,
    Pending,
    Failed,
    Empty,
}

public static class ExtractedTextStatus
{
    /// <summary>
    /// Maps the endpoint's <c>extraction_status</c> + payload to what the pane
    /// should show. Only the terminal "completed" status may be read as text;
    /// any non-terminal status is Pending even when the text field is blank.
    /// </summary>
    public static ExtractedTextState Resolve(string? extractionStatus, string? text)
    {
        var status = (extractionStatus ?? string.Empty).Trim().ToLowerInvariant();
        return status switch
        {
            "completed" or "complete" or "done" or "succeeded" =>
                string.IsNullOrWhiteSpace(text) ? ExtractedTextState.Empty : ExtractedTextState.Ready,
            "failed" or "error" or "unsupported" => ExtractedTextState.Failed,
            _ => ExtractedTextState.Pending,
        };
    }
}
