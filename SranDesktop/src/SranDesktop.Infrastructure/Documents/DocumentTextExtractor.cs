using System.Text;
using ClosedXML.Excel;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;
using UglyToad.PdfPig;
using UglyToad.PdfPig.DocumentLayoutAnalysis.TextExtractor;
using A = DocumentFormat.OpenXml.Drawing;

namespace SranDesktop.Infrastructure.Documents;

/// <summary>
/// Local text extraction for the document formats an office worker actually
/// has on disk. Pure library work — no shelling out, no server round-trip, so
/// it runs on a laptop with no Office install and inside the PathGuard the
/// caller already cleared.
///
/// Everything here is managed-only by design: PdfPig (Apache-2.0) for PDF,
/// DocumentFormat.OpenXml (MIT) for DOCX/PPTX, ClosedXML (MIT) for XLSX. A
/// pdfium/poppler wrapper would have needed a native binary next to the exe,
/// which the per-user installer cannot reliably place.
///
/// NOT in scope: OCR. A scanned PDF or a photographed page has no text layer,
/// and this class says so explicitly rather than returning empty output that
/// reads to the model as "the document is blank".
/// </summary>
public static class DocumentTextExtractor
{
    /// <summary>Output budget. Below ToolResult's 50 000-char hard cap so the
    /// truncation notice this class writes (with the real page/sheet totals)
    /// survives instead of being cut off by the generic truncator.</summary>
    public const int DefaultMaxChars = 40_000;

    /// <summary>Guards against a pathological spreadsheet (a million empty-ish
    /// rows) turning one read into a multi-minute stall.</summary>
    private const int MaxRowsPerSheet = 500;

    private static readonly HashSet<string> PlainTextExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".json", ".xml",
        ".yaml", ".yml", ".ini", ".rtf",
    };

    /// <summary>Legacy binary Office formats. OpenXml cannot read them and no
    /// managed reader exists that is worth the dependency, so we fail with the
    /// one instruction that actually fixes it.</summary>
    private static readonly HashSet<string> LegacyOfficeExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".doc", ".xls", ".ppt",
    };

    public static bool IsSupported(string path) =>
        Path.GetExtension(path).ToLowerInvariant() switch
        {
            ".pdf" or ".docx" or ".xlsx" or ".xlsm" or ".pptx" => true,
            var ext => PlainTextExtensions.Contains(ext),
        };

    /// <summary>Extracts readable text. Throws nothing for unreadable content —
    /// failures come back as <see cref="DocumentExtraction.Error"/> so the tool
    /// layer can hand the model a sentence it can act on.</summary>
    public static DocumentExtraction Extract(string path, int maxChars = DefaultMaxChars)
    {
        var ext = Path.GetExtension(path).ToLowerInvariant();

        if (LegacyOfficeExtensions.Contains(ext))
        {
            return DocumentExtraction.Failed(
                $"'{Path.GetFileName(path)}' is a legacy binary Office file ({ext}). " +
                "Open it in Office/LibreOffice and save as the modern equivalent " +
                $"({ext switch { ".doc" => ".docx", ".xls" => ".xlsx", _ => ".pptx" }}), then read it again.");
        }

        try
        {
            return ext switch
            {
                ".pdf" => ExtractPdf(path, maxChars),
                ".docx" => ExtractDocx(path, maxChars),
                ".xlsx" or ".xlsm" => ExtractXlsx(path, maxChars),
                ".pptx" => ExtractPptx(path, maxChars),
                _ when PlainTextExtensions.Contains(ext) => ExtractPlainText(path, maxChars),
                _ => DocumentExtraction.Failed(
                    $"Unsupported file type '{ext}'. read_document handles PDF, DOCX, XLSX, PPTX " +
                    "and plain-text formats; use read_file for anything else."),
            };
        }
        catch (UnauthorizedAccessException)
        {
            return DocumentExtraction.Failed(
                $"No permission to read '{Path.GetFileName(path)}'.");
        }
        catch (IOException ex)
        {
            // Almost always "open in Word/Excel right now" — say the thing the
            // user can act on rather than surfacing a raw HRESULT.
            return DocumentExtraction.Failed(
                $"Could not open '{Path.GetFileName(path)}': {ex.Message} " +
                "If the file is open in another program, close it and try again.");
        }
        // Deliberately broad: this is the boundary where three third-party
        // parsers meet arbitrary user files. Each throws its own type for a
        // malformed document (PdfPig alone raises PdfDocumentFormatException,
        // which an `is X or Y` list silently missed until a test caught it),
        // and a parse failure must never escape into the agent loop and kill
        // the turn. Cancellation and OOM still propagate — they are not
        // "this document is bad", they are "stop".
        catch (Exception ex) when (ex is not OperationCanceledException
                                       and not OutOfMemoryException)
        {
            return DocumentExtraction.Failed(
                $"Could not read '{Path.GetFileName(path)}': {ex.Message} " +
                "The file may be corrupt, password-protected, or not actually the type its extension claims.");
        }
    }

    // ── PDF ───────────────────────────────────────────────────────────────

    private static DocumentExtraction ExtractPdf(string path, int maxChars)
    {
        using var pdf = PdfDocument.Open(path);
        var total = pdf.NumberOfPages;
        var sb = new StringBuilder();
        var pagesIncluded = 0;
        var anyText = false;

        for (var i = 1; i <= total; i++)
        {
            var page = pdf.GetPage(i);
            string text;
            try
            {
                // Content-order extraction reads far closer to how a human sees
                // the page than raw letter order (columns stay columns).
                text = ContentOrderTextExtractor.GetText(page) ?? string.Empty;
            }
            catch (Exception ex) when (ex is InvalidOperationException or NotSupportedException)
            {
                text = page.Text ?? string.Empty; // fall back to raw letter order
            }

            text = NormalizeWhitespace(text);
            if (text.Length > 0)
                anyText = true;

            var block = $"--- Page {i} of {total} ---\n{(text.Length == 0 ? "[no text on this page]" : text)}\n\n";
            if (sb.Length + block.Length > maxChars && pagesIncluded > 0)
                break;

            sb.Append(block);
            pagesIncluded++;
        }

        if (!anyText)
        {
            // A scan. This class stays offline-only, so it reports the finding
            // rather than acting on it: the TOOL decides whether to fall back
            // to the server's OCR (which needs the user's consent, because that
            // is the one path where a local read leaves the machine). The Error
            // sentence is still filled in for callers with no OCR route.
            return DocumentExtraction.RequiresOcr(
                $"'{Path.GetFileName(path)}' has {total} page(s) but no extractable text layer — " +
                "it is almost certainly a scan or a photo of a document. " +
                "Reading scanned pages needs OCR, which is not available offline. " +
                "Ask the user for a text-based PDF, or for the original document.",
                total);
        }

        return new DocumentExtraction(
            Kind: "PDF",
            Text: sb.ToString().TrimEnd(),
            UnitLabel: "page",
            TotalUnits: total,
            UnitsIncluded: pagesIncluded);
    }

    // ── Word ──────────────────────────────────────────────────────────────

    private static DocumentExtraction ExtractDocx(string path, int maxChars)
    {
        using var doc = WordprocessingDocument.Open(path, false);
        var body = doc.MainDocumentPart?.Document?.Body;
        if (body is null)
            return DocumentExtraction.Failed($"'{Path.GetFileName(path)}' has no document body.");

        var sb = new StringBuilder();
        var blocks = 0;
        var totalBlocks = 0;
        var truncated = false;

        foreach (var element in body.ChildElements)
        {
            string? rendered = element switch
            {
                Paragraph p => RenderParagraph(p),
                Table t => RenderTable(t),
                _ => null,
            };
            if (rendered is null)
                continue;

            totalBlocks++;
            if (truncated)
                continue;

            if (sb.Length + rendered.Length > maxChars && blocks > 0)
            {
                truncated = true;
                continue;
            }
            sb.Append(rendered);
            blocks++;
        }

        return new DocumentExtraction(
            Kind: "Word document",
            Text: sb.ToString().TrimEnd(),
            UnitLabel: "block",
            TotalUnits: totalBlocks,
            UnitsIncluded: blocks);
    }

    private static string? RenderParagraph(Paragraph p)
    {
        var text = NormalizeWhitespace(p.InnerText);
        if (text.Length == 0)
            return null;

        // Surface heading levels as markdown so the model sees the structure
        // rather than one flat wall of sentences.
        var styleId = p.ParagraphProperties?.ParagraphStyleId?.Val?.Value ?? string.Empty;
        if (styleId.StartsWith("Heading", StringComparison.OrdinalIgnoreCase)
            && int.TryParse(styleId.AsSpan("Heading".Length), out var level)
            && level is >= 1 and <= 6)
        {
            return new string('#', level) + " " + text + "\n\n";
        }
        return text + "\n\n";
    }

    private static string RenderTable(Table table)
    {
        var sb = new StringBuilder();
        foreach (var row in table.Elements<TableRow>())
        {
            var cells = row.Elements<TableCell>()
                .Select(c => NormalizeWhitespace(c.InnerText).Replace('\n', ' '));
            sb.Append("| ").Append(string.Join(" | ", cells)).Append(" |\n");
        }
        if (sb.Length > 0)
            sb.Append('\n');
        return sb.ToString();
    }

    // ── Excel ─────────────────────────────────────────────────────────────

    private static DocumentExtraction ExtractXlsx(string path, int maxChars)
    {
        using var wb = new XLWorkbook(path);
        var sheets = wb.Worksheets.ToList();
        var sb = new StringBuilder();
        var sheetsIncluded = 0;

        foreach (var ws in sheets)
        {
            var used = ws.RangeUsed();
            var header = $"### Sheet: {ws.Name}\n";
            if (used is null)
            {
                var empty = header + "[empty sheet]\n\n";
                if (sb.Length + empty.Length > maxChars && sheetsIncluded > 0)
                    break;
                sb.Append(empty);
                sheetsIncluded++;
                continue;
            }

            var rows = used.RowsUsed().ToList();
            var block = new StringBuilder(header);
            block.Append($"({rows.Count} row(s) × {used.ColumnCount()} column(s))\n");

            var emitted = 0;
            foreach (var row in rows)
            {
                if (emitted >= MaxRowsPerSheet)
                {
                    block.Append($"[... {rows.Count - emitted} more row(s) not shown]\n");
                    break;
                }
                var cells = row.Cells(1, used.ColumnCount())
                    .Select(c => NormalizeWhitespace(c.GetFormattedString()).Replace('\n', ' '));
                block.Append("| ").Append(string.Join(" | ", cells)).Append(" |\n");
                emitted++;
            }
            block.Append('\n');

            if (sb.Length + block.Length > maxChars && sheetsIncluded > 0)
                break;
            sb.Append(block);
            sheetsIncluded++;
        }

        return new DocumentExtraction(
            Kind: "Excel workbook",
            Text: sb.ToString().TrimEnd(),
            UnitLabel: "sheet",
            TotalUnits: sheets.Count,
            UnitsIncluded: sheetsIncluded);
    }

    // ── PowerPoint ────────────────────────────────────────────────────────

    private static DocumentExtraction ExtractPptx(string path, int maxChars)
    {
        using var doc = PresentationDocument.Open(path, false);
        var presentation = doc.PresentationPart;
        var slideParts = presentation?.SlideParts.ToList() ?? new List<SlidePart>();
        var sb = new StringBuilder();
        var slidesIncluded = 0;

        for (var i = 0; i < slideParts.Count; i++)
        {
            var texts = TextsOf(slideParts[i].Slide);
            var notes = TextsOf(slideParts[i].NotesSlidePart?.NotesSlide);

            var block = new StringBuilder($"--- Slide {i + 1} of {slideParts.Count} ---\n");
            block.Append(texts.Count == 0 ? "[no text on this slide]\n" : string.Join("\n", texts) + "\n");
            if (notes.Count > 0)
                block.Append("Speaker notes: ").Append(string.Join(" ", notes)).Append('\n');
            block.Append('\n');

            if (sb.Length + block.Length > maxChars && slidesIncluded > 0)
                break;
            sb.Append(block);
            slidesIncluded++;
        }

        return new DocumentExtraction(
            Kind: "PowerPoint deck",
            Text: sb.ToString().TrimEnd(),
            UnitLabel: "slide",
            TotalUnits: slideParts.Count,
            UnitsIncluded: slidesIncluded);
    }

    /// <summary>All non-empty drawing-text runs under a slide part. A slide or
    /// notes part can legitimately be absent, so null in means empty out.</summary>
    private static List<string> TextsOf(DocumentFormat.OpenXml.OpenXmlPartRootElement? root) =>
        root is null
            ? new List<string>()
            : root.Descendants<A.Text>()
                .Select(t => NormalizeWhitespace(t.Text))
                .Where(t => t.Length > 0)
                .ToList();

    // ── Plain text ────────────────────────────────────────────────────────

    private static DocumentExtraction ExtractPlainText(string path, int maxChars)
    {
        var text = File.ReadAllText(path);
        var truncated = text.Length > maxChars;
        var lines = text.AsSpan().Count('\n') + 1;
        return new DocumentExtraction(
            Kind: "text file",
            Text: truncated ? text[..maxChars] : text,
            UnitLabel: "line",
            TotalUnits: lines,
            // Line accounting is approximate for the truncated case; the header
            // only needs to tell the model that something was cut.
            UnitsIncluded: truncated ? text[..maxChars].AsSpan().Count('\n') + 1 : lines);
    }

    // ── Shared ────────────────────────────────────────────────────────────

    /// <summary>Collapses the runs of spaces/blank lines that PDF and OpenXML
    /// extraction leave behind — they are pure context cost to the model.</summary>
    private static string NormalizeWhitespace(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
            return string.Empty;

        var sb = new StringBuilder(raw.Length);
        var blankRun = 0;
        foreach (var line in raw.Replace("\r\n", "\n").Replace('\r', '\n').Split('\n'))
        {
            var trimmed = line.TrimEnd();
            if (trimmed.Length == 0)
            {
                if (++blankRun > 1)
                    continue; // at most one blank line in a row
            }
            else
            {
                blankRun = 0;
            }
            sb.Append(trimmed).Append('\n');
        }
        return sb.ToString().Trim();
    }
}

/// <summary>Outcome of one extraction. <see cref="Error"/> is non-null only
/// when nothing usable came out.</summary>
public sealed record DocumentExtraction(
    string Kind,
    string Text,
    string UnitLabel,
    int TotalUnits,
    int UnitsIncluded,
    string? Error = null,
    bool NeedsOcr = false)
{
    public static DocumentExtraction Failed(string error) =>
        new("unknown", string.Empty, "unit", 0, 0, error);

    /// <summary>A failure the caller may be able to recover from by sending the
    /// document to an OCR service. <paramref name="pageCount"/> travels along so
    /// the caller can size the work before deciding.</summary>
    public static DocumentExtraction RequiresOcr(string error, int pageCount) =>
        new("PDF", string.Empty, "page", pageCount, 0, error, NeedsOcr: true);

    public bool Success => Error is null;

    /// <summary>True when the budget cut the document short — the model must be
    /// told, or it will summarize a partial document as if it were the whole.</summary>
    public bool Truncated => UnitsIncluded < TotalUnits;
}
