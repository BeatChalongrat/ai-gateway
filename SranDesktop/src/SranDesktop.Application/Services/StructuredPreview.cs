using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>Which structured view an attachment gets (Phase 16), on top of
/// the <see cref="AttachmentPreviewKind"/> routing from Phase 14.</summary>
public enum StructuredPreviewKind
{
    /// <summary>A workbook or a delimited text file — drawn as a grid.</summary>
    Sheet,

    /// <summary>Word — headings, paragraphs, lists and tables in order.</summary>
    Document,

    /// <summary>PowerPoint — one panel per slide.</summary>
    Slides,
}

/// <summary>
/// Decides whether to ASK the server for structured data, and merges the
/// windows it returns.
///
/// Deliberately separate from <see cref="AttachmentPreviewRouter"/> rather
/// than folded into it: this is a hint for an optional request. A file that
/// qualifies here still falls back to the extracted text when the server
/// answers 415 (a type it cannot read) or 503 (no sandbox on this
/// deployment), so the Phase 14 routing has to keep working untouched.
///
/// Content type first, extension second — the same precedence the server
/// uses (service.PreviewDataKind), because uploads regularly arrive as
/// application/octet-stream.
/// </summary>
public static class StructuredPreviewRouter
{
    private static readonly Dictionary<string, StructuredPreviewKind> Types =
        new(StringComparer.OrdinalIgnoreCase)
        {
            ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"] = StructuredPreviewKind.Sheet,
            ["application/vnd.ms-excel.sheet.macroEnabled.12"] = StructuredPreviewKind.Sheet,
            ["text/csv"] = StructuredPreviewKind.Sheet,
            ["application/csv"] = StructuredPreviewKind.Sheet,
            ["text/tab-separated-values"] = StructuredPreviewKind.Sheet,
            ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"] = StructuredPreviewKind.Document,
            ["application/vnd.openxmlformats-officedocument.presentationml.presentation"] = StructuredPreviewKind.Slides,
        };

    private static readonly Dictionary<string, StructuredPreviewKind> Extensions =
        new(StringComparer.OrdinalIgnoreCase)
        {
            [".xlsx"] = StructuredPreviewKind.Sheet,
            [".xlsm"] = StructuredPreviewKind.Sheet,
            [".csv"] = StructuredPreviewKind.Sheet,
            [".tsv"] = StructuredPreviewKind.Sheet,
            [".docx"] = StructuredPreviewKind.Document,
            [".pptx"] = StructuredPreviewKind.Slides,
        };

    public static StructuredPreviewKind? Resolve(string? contentType, string? filename)
    {
        var type = (contentType ?? string.Empty).Trim();
        var semicolon = type.IndexOf(';');
        if (semicolon >= 0)
            type = type[..semicolon];
        if (Types.TryGetValue(type.Trim(), out var byType))
            return byType;

        var extension = SafeExtension(filename);
        if (extension.Length > 0 && Extensions.TryGetValue(extension, out var byExtension))
            return byExtension;

        return null;
    }

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

/// <summary>How much one "load more" fetches. Matches the web client's
/// PREVIEW_PAGE_* so both surfaces page at the same rate, and stays at or
/// under the server's own caps (service.PreviewMaxRows).</summary>
public static class PreviewPageSize
{
    public const int Rows = 200;
    public const int Blocks = 150;
    public const int Slides = 25;
}

/// <summary>
/// Merges a newly fetched window onto what is already on screen.
///
/// The merged view keeps the ORIGINAL offset while taking the new window's
/// metadata (total, has_more). That is what keeps row numbers true after
/// several pages: row labels are computed from the accumulated offset, so
/// adopting the new window's offset would restart the numbering at the
/// start of the newest page.
/// </summary>
public static class PreviewWindowMerge
{
    public static SheetPreviewDto Append(SheetPreviewDto current, SheetPreviewDto next)
    {
        var rows = new List<List<string>>(current.Rows ?? new List<List<string>>());
        rows.AddRange(next.Rows ?? new List<List<string>>());
        return next with { Offset = current.Offset, Rows = rows };
    }

    public static DocumentPreviewDto Append(DocumentPreviewDto current, DocumentPreviewDto next)
    {
        var blocks = new List<PreviewBlockDto>(current.Blocks ?? new List<PreviewBlockDto>());
        blocks.AddRange(next.Blocks ?? new List<PreviewBlockDto>());
        return next with { Offset = current.Offset, Blocks = blocks };
    }

    public static SlidesPreviewDto Append(SlidesPreviewDto current, SlidesPreviewDto next)
    {
        var slides = new List<SlidePreviewDto>(current.Slides ?? new List<SlidePreviewDto>());
        slides.AddRange(next.Slides ?? new List<SlidePreviewDto>());
        return next with { Offset = current.Offset, Slides = slides };
    }

    /// <summary>Where the NEXT window starts: one past what is on screen.</summary>
    public static int NextOffset(int offset, int loaded) => offset + loaded;

    /// <summary>Excel-style 1-based label for the row at <paramref name="index"/>
    /// of a window that began at <paramref name="offset"/>.</summary>
    public static string RowLabel(int offset, int index) => (offset + index + 1).ToString();
}
