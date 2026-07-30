using System.Text;
using System.Text.Json;
using ClosedXML.Excel;
using DocumentFormat.OpenXml.Packaging;
using D = DocumentFormat.OpenXml.Drawing;
using P = DocumentFormat.OpenXml.Presentation;
using W = DocumentFormat.OpenXml.Wordprocessing;

namespace SranDesktop.DocumentWorker;

internal sealed record ValidationFinding(string Code, string Detail, bool Blocking);

internal sealed record ValidationResult(
    bool Reopened,
    bool StructureValid,
    IReadOnlyList<ValidationFinding> Findings,
    int? Count,
    IReadOnlyList<WorkerObservation> Observations)
{
    public bool Passed =>
        Reopened && StructureValid && Findings.All(finding => !finding.Blocking);

    public string Summary =>
        Findings.Count == 0
            ? (Passed ? "validated" : "the document could not be reopened")
            : string.Join("; ", Findings.Select(f => $"{f.Code}: {f.Detail}"));

    public string ToJson() => JsonSerializer.Serialize(
        new
        {
            reopened = Reopened,
            structure_valid = StructureValid,
            count = Count,
            findings = Findings.Select(f => new
            {
                code = f.Code,
                detail = f.Detail,
                blocking = f.Blocking
            })
        },
        WorkerJson.Options);
}

/// <summary>
/// Deterministic post-write verification. Everything here works by REOPENING the
/// produced file with the same parser the user's application would use — the file
/// existing, or the writer not throwing, proves nothing about whether it opens.
/// </summary>
internal static class DocumentValidator
{
    public static ValidationResult Validate(string path, string format, JsonElement payload)
    {
        if (!File.Exists(path))
            return new ValidationResult(
                false,
                false,
                [new ValidationFinding("OutputMissing", "No file was produced.", true)],
                null,
                []);
        if (new FileInfo(path).Length == 0)
            return new ValidationResult(
                false,
                false,
                [new ValidationFinding("OutputEmpty", "The produced file is zero bytes.", true)],
                null,
                []);

        try
        {
            return format switch
            {
                "docx" => ValidateDocx(path, payload),
                "xlsx" => ValidateXlsx(path, payload),
                "pptx" => ValidatePptx(path, payload),
                "pdf" => ValidatePdf(path, payload),
                _ => new ValidationResult(
                    false,
                    false,
                    [new ValidationFinding(
                        "UnsupportedFormat", $"No validator for '{format}'.", true)],
                    null,
                    [])
            };
        }
        catch (Exception ex)
        {
            // A parser throwing IS the finding: the package is corrupt.
            return new ValidationResult(
                false,
                false,
                [new ValidationFinding(
                    "DocumentCorrupted",
                    $"Reopening failed: {ex.GetType().Name}: {ex.Message}",
                    true)],
                null,
                []);
        }
    }

    private static ValidationResult ValidateDocx(string path, JsonElement payload)
    {
        var findings = new List<ValidationFinding>();
        var observations = new List<WorkerObservation>();
        using var document = WordprocessingDocument.Open(path, isEditable: false);
        var body = document.MainDocumentPart?.Document?.Body;
        if (body is null)
        {
            findings.Add(new ValidationFinding(
                "MissingBody", "The package has no document body.", true));
            return new ValidationResult(true, false, findings, null, observations);
        }

        var paragraphs = body.Elements<W.Paragraph>().ToList();
        var tables = body.Elements<W.Table>().ToList();
        var text = new StringBuilder();
        foreach (var run in body.Descendants<W.Text>())
            text.Append(run.Text);
        var content = text.ToString();

        if (paragraphs.Count == 0 && tables.Count == 0)
            findings.Add(new ValidationFinding(
                "EmptyDocument", "The document contains no paragraphs or tables.", true));

        observations.Add(new WorkerObservation(
            "docx_structure",
            $"paragraphs={paragraphs.Count}; tables={tables.Count}; characters={content.Length}"));
        AppendExpectations(payload, content, findings, tables.Count, paragraphs.Count);

        return new ValidationResult(
            true, findings.All(f => !f.Blocking), findings, paragraphs.Count, observations);
    }

    private static ValidationResult ValidateXlsx(string path, JsonElement payload)
    {
        var findings = new List<ValidationFinding>();
        var observations = new List<WorkerObservation>();
        using var workbook = new XLWorkbook(path);
        var sheets = workbook.Worksheets.ToList();
        if (sheets.Count == 0)
            findings.Add(new ValidationFinding(
                "NoWorksheets", "The workbook has no worksheets and will not open.", true));

        var formulaCount = 0;
        foreach (var sheet in sheets)
        {
            var used = sheet.RangeUsed();
            formulaCount += used is null
                ? 0
                : used.Cells().Count(cell => cell.HasFormula);
        }
        observations.Add(new WorkerObservation(
            "xlsx_structure",
            $"sheets={sheets.Count}; names={string.Join(",", sheets.Select(s => s.Name))}; "
            + $"formulas={formulaCount}"));

        if (payload.TryGetProperty("expect", out var expect)
            && expect.ValueKind == JsonValueKind.Object)
        {
            if (expect.TryGetProperty("sheets", out var expectedSheets)
                && expectedSheets.ValueKind == JsonValueKind.Array)
            {
                foreach (var name in expectedSheets.EnumerateArray()
                             .Select(item => item.GetString())
                             .Where(name => !string.IsNullOrEmpty(name)))
                {
                    if (!sheets.Any(sheet =>
                            string.Equals(sheet.Name, name, StringComparison.OrdinalIgnoreCase)))
                        findings.Add(new ValidationFinding(
                            "ExpectedSheetMissing", $"Sheet '{name}' is not present.", true));
                }
            }
            if (expect.TryGetProperty("cells", out var expectedCells)
                && expectedCells.ValueKind == JsonValueKind.Array)
            {
                foreach (var cell in expectedCells.EnumerateArray())
                {
                    var sheetName = cell.TryGetProperty("sheet", out var s)
                        ? s.GetString()
                        : null;
                    var reference = cell.TryGetProperty("cell", out var c) ? c.GetString() : null;
                    var expectedValue = cell.TryGetProperty("value", out var v)
                        ? v.ToString()
                        : null;
                    if (reference is null || expectedValue is null)
                        continue;
                    var sheet = sheetName is null
                        ? sheets.FirstOrDefault()
                        : sheets.FirstOrDefault(item =>
                            string.Equals(item.Name, sheetName, StringComparison.OrdinalIgnoreCase));
                    var actual = sheet?.Cell(reference).GetFormattedString();
                    if (!string.Equals(actual, expectedValue, StringComparison.Ordinal))
                        findings.Add(new ValidationFinding(
                            "CellValueMismatch",
                            $"{sheetName ?? "(first sheet)"}!{reference} read back as "
                            + $"'{actual}', expected '{expectedValue}'.",
                            true));
                }
            }
        }

        return new ValidationResult(
            true, findings.All(f => !f.Blocking), findings, sheets.Count, observations);
    }

    private static ValidationResult ValidatePptx(string path, JsonElement payload)
    {
        var findings = new List<ValidationFinding>();
        var observations = new List<WorkerObservation>();
        using var document = PresentationDocument.Open(path, isEditable: false);
        var presentationPart = document.PresentationPart;
        var slideIds = presentationPart?.Presentation?
            .GetFirstChild<P.SlideIdList>()?.Elements<P.SlideId>().ToList() ?? [];
        if (slideIds.Count == 0)
            findings.Add(new ValidationFinding(
                "NoSlides", "The deck has no slides and will not open.", true));

        var slideWidth = presentationPart?.Presentation?
            .GetFirstChild<P.SlideSize>()?.Cx?.Value ?? 12192000;
        var slideHeight = presentationPart?.Presentation?
            .GetFirstChild<P.SlideSize>()?.Cy?.Value ?? 6858000;

        var blankSlides = 0;
        var index = 0;
        foreach (var slideId in slideIds)
        {
            var slidePart = (SlidePart)presentationPart!.GetPartById(slideId.RelationshipId!.Value!);
            var slideText = string.Concat(
                slidePart.Slide.Descendants<D.Text>().Select(item => item.Text));
            if (string.IsNullOrWhiteSpace(slideText))
                blankSlides++;

            // Deterministic layout check: a shape positioned past the slide edge
            // renders clipped in PowerPoint even though the package is valid.
            foreach (var transform in slidePart.Slide.Descendants<D.Transform2D>())
            {
                var offset = transform.Offset;
                var extents = transform.Extents;
                if (offset is null || extents is null)
                    continue;
                var right = (offset.X?.Value ?? 0) + (extents.Cx?.Value ?? 0);
                var bottom = (offset.Y?.Value ?? 0) + (extents.Cy?.Value ?? 0);
                if (right > slideWidth || bottom > slideHeight
                    || (offset.X?.Value ?? 0) < 0 || (offset.Y?.Value ?? 0) < 0)
                    findings.Add(new ValidationFinding(
                        "ShapeOutOfBounds",
                        $"Slide {index + 1} has a shape extending past the slide area.",
                        true));
            }
            index++;
        }

        if (blankSlides > 0 && !ExpectsBlankSlides(payload))
            findings.Add(new ValidationFinding(
                "BlankSlide",
                $"{blankSlides} slide(s) contain no text.",
                Blocking: slideIds.Count == blankSlides));

        observations.Add(new WorkerObservation(
            "pptx_structure",
            $"slides={slideIds.Count}; blank={blankSlides}"));
        return new ValidationResult(
            true, findings.All(f => !f.Blocking), findings, slideIds.Count, observations);
    }

    /// <summary>
    /// PDF is treated as hostile input: the bytes are parsed structurally, never
    /// executed, and no embedded action, JavaScript, or external reference is
    /// followed. An encrypted file is reported as unsupported rather than guessed at.
    /// </summary>
    private static ValidationResult ValidatePdf(string path, JsonElement payload)
    {
        _ = payload;
        var findings = new List<ValidationFinding>();
        var observations = new List<WorkerObservation>();
        var bytes = File.ReadAllBytes(path);
        if (bytes.Length < 5
            || Encoding.ASCII.GetString(bytes, 0, 5) != "%PDF-")
        {
            findings.Add(new ValidationFinding(
                "NotAPdf", "The file does not start with a PDF signature.", true));
            return new ValidationResult(false, false, findings, null, observations);
        }

        var text = Encoding.Latin1.GetString(bytes);
        // "/Type /Pages" is the page TREE node, not a page. Counting it inflated
        // every page count by one, which is the kind of error that looks like a
        // rounding quirk until somebody splits a document on it.
        var pageCount = CountPageObjects(text, "/Type/Page")
                        + CountPageObjects(text, "/Type /Page");
        var encrypted = text.Contains("/Encrypt", StringComparison.Ordinal);
        var hasJavaScript = text.Contains("/JavaScript", StringComparison.Ordinal)
                            || text.Contains("/JS", StringComparison.Ordinal);
        var hasEmbeddedFiles = text.Contains("/EmbeddedFile", StringComparison.Ordinal);
        var hasLaunchAction = text.Contains("/Launch", StringComparison.Ordinal);
        var hasXref = text.Contains("xref", StringComparison.Ordinal)
                      || text.Contains("/XRef", StringComparison.Ordinal);

        if (encrypted)
            findings.Add(new ValidationFinding(
                "EncryptedPdf",
                "The PDF is encrypted; a password is required and cannot be guessed.",
                true));
        if (!hasXref)
            findings.Add(new ValidationFinding(
                "MissingXref", "The cross-reference table is absent or damaged.", true));
        if (pageCount == 0 && !encrypted)
            findings.Add(new ValidationFinding(
                "NoPages", "No page objects were found.", true));

        // Reported, never acted on — these are the reasons PDF content stays an
        // untrusted observation.
        if (hasJavaScript)
            observations.Add(new WorkerObservation(
                "pdf_active_content",
                "The PDF declares JavaScript. It was NOT executed."));
        if (hasEmbeddedFiles)
            observations.Add(new WorkerObservation(
                "pdf_embedded_files",
                "The PDF contains embedded files. They were NOT extracted."));
        if (hasLaunchAction)
            observations.Add(new WorkerObservation(
                "pdf_launch_action",
                "The PDF declares a launch action. It was NOT followed."));

        observations.Add(new WorkerObservation(
            "pdf_structure",
            $"pages={pageCount}; bytes={bytes.Length}; encrypted={encrypted}"));
        return new ValidationResult(
            true, findings.All(f => !f.Blocking), findings, pageCount, observations);
    }

    private static void AppendExpectations(
        JsonElement payload,
        string content,
        List<ValidationFinding> findings,
        int tableCount,
        int paragraphCount)
    {
        if (!payload.TryGetProperty("expect", out var expect)
            || expect.ValueKind != JsonValueKind.Object)
            return;

        if (expect.TryGetProperty("contains_text", out var texts)
            && texts.ValueKind == JsonValueKind.Array)
        {
            foreach (var value in texts.EnumerateArray()
                         .Select(item => item.GetString())
                         .Where(value => !string.IsNullOrEmpty(value)))
            {
                if (!content.Contains(value!, StringComparison.Ordinal))
                    findings.Add(new ValidationFinding(
                        "ExpectedTextMissing",
                        $"The reopened document does not contain '{value}'.",
                        true));
            }
        }
        if (expect.TryGetProperty("absent_text", out var absent)
            && absent.ValueKind == JsonValueKind.Array)
        {
            foreach (var value in absent.EnumerateArray()
                         .Select(item => item.GetString())
                         .Where(value => !string.IsNullOrEmpty(value)))
            {
                if (content.Contains(value!, StringComparison.Ordinal))
                    findings.Add(new ValidationFinding(
                        "UnexpectedTextPresent",
                        $"The reopened document still contains '{value}'.",
                        true));
            }
        }
        if (expect.TryGetProperty("min_tables", out var minTables)
            && minTables.TryGetInt32(out var requiredTables)
            && tableCount < requiredTables)
            findings.Add(new ValidationFinding(
                "TableCountTooLow",
                $"Expected at least {requiredTables} table(s), found {tableCount}.",
                true));
        if (expect.TryGetProperty("min_paragraphs", out var minParagraphs)
            && minParagraphs.TryGetInt32(out var requiredParagraphs)
            && paragraphCount < requiredParagraphs)
            findings.Add(new ValidationFinding(
                "ParagraphCountTooLow",
                $"Expected at least {requiredParagraphs} paragraph(s), found {paragraphCount}.",
                true));
    }

    private static bool ExpectsBlankSlides(JsonElement payload) =>
        payload.TryGetProperty("expect", out var expect)
        && expect.ValueKind == JsonValueKind.Object
        && expect.TryGetProperty("allow_blank_slides", out var allow)
        && allow.ValueKind == JsonValueKind.True;

    /// <summary>Counts page objects, skipping the "/Pages" tree node that shares
    /// the prefix.</summary>
    private static int CountPageObjects(string haystack, string needle)
    {
        var count = 0;
        for (var index = haystack.IndexOf(needle, StringComparison.Ordinal);
             index >= 0;
             index = haystack.IndexOf(needle, index + needle.Length, StringComparison.Ordinal))
        {
            var after = index + needle.Length;
            if (after < haystack.Length && haystack[after] == 's')
                continue;
            count++;
        }
        return count;
    }

    private static int CountOccurrences(string haystack, string needle)
    {
        var count = 0;
        for (var index = haystack.IndexOf(needle, StringComparison.Ordinal);
             index >= 0;
             index = haystack.IndexOf(needle, index + needle.Length, StringComparison.Ordinal))
            count++;
        return count;
    }
}
