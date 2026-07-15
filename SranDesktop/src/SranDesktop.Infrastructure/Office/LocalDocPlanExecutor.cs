using System.Text.Json;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Office;

/// <summary>
/// On-device :::doc-plan executor (Phase 12 E) for format=docx, interpreting
/// the same plan JSON as the server's scripts/docx_generator.py through
/// DocumentFormat.OpenXml (MIT).
///
/// Supported locally: create-mode plans with add_heading / add_paragraph /
/// add_bullet_list / add_numbered_list / add_table / add_page_break /
/// add_horizontal_rule, plus document_settings page size / orientation /
/// margins / language / font_family / base_font_size (Thai gets the same
/// Sarabun + complex-script font tagging the server applies).
/// NOT supported (CanExecute false → server fallback): pdf/pptx formats,
/// source_file_id edits, replace_text, set_header / set_footer and the
/// header_text / footer_text settings (Word header parts stay server-side).
/// </summary>
public sealed class LocalDocPlanExecutor : ILocalPlanExecutor
{
    private const string CorporateNavyHex = "0A1230";
    private const int BulletNumId = 1;
    private const int DecimalNumId = 2;

    private static readonly HashSet<string> SupportedActions = new(StringComparer.Ordinal)
    {
        "add_heading", "add_paragraph", "add_bullet_list", "add_numbered_list",
        "add_table", "add_page_break", "add_horizontal_rule",
    };

    public string PlanType => "doc";

    public bool CanExecute(JsonElement plan)
    {
        if (plan.ValueKind != JsonValueKind.Object)
            return false;
        // Edit mode copies the uploaded source server-side — fall back.
        if (!string.IsNullOrEmpty(PlanJson.GetString(plan, "source_file_id")))
            return false;
        // pdf (reportlab) and pptx (python-pptx) stay server-only this phase.
        if ((PlanJson.GetString(plan, "format") ?? "docx") != "docx")
            return false;
        // Header/footer parts (incl. {page}/{total} field codes) stay server-side.
        if (PlanJson.TryGetObject(plan, "document_settings", out var settings)
            && (!string.IsNullOrEmpty(PlanJson.GetString(settings, "header_text"))
                || !string.IsNullOrEmpty(PlanJson.GetString(settings, "footer_text"))))
            return false;

        if (PlanJson.TryGetArray(plan, "actions", out var actions))
        {
            foreach (var action in actions.EnumerateArray())
            {
                if (action.ValueKind != JsonValueKind.Object)
                    return false;
                var type = PlanJson.GetString(action, "type");
                if (type is null || !SupportedActions.Contains(type))
                    return false; // replace_text / set_header / set_footer / unknown
            }
        }
        return true;
    }

    public Task<LocalPlanExecutionResult> ExecuteAsync(
        JsonElement plan, string outputPath, CancellationToken ct = default)
        => Task.Run(() => Execute(plan, outputPath, ct), ct);

    private static LocalPlanExecutionResult Execute(
        JsonElement plan, string outputPath, CancellationToken ct)
    {
        var fullPath = Path.GetFullPath(outputPath);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);

        // Temp sibling + move-on-success: no corrupt file on failure.
        var tempPath = Path.Combine(
            Path.GetDirectoryName(fullPath)!,
            $"{Path.GetFileNameWithoutExtension(fullPath)}.{Guid.NewGuid():N}.tmp{Path.GetExtension(fullPath)}");
        try
        {
            PlanJson.TryGetObject(plan, "document_settings", out var settings);
            var (font, baseSizePt) = ResolveFont(settings);

            var changes = new List<string>();
            var wordCount = 0;
            using (var doc = WordprocessingDocument.Create(
                       tempPath, WordprocessingDocumentType.Document))
            {
                var main = doc.AddMainDocumentPart();
                var body = new Body();
                main.Document = new Document(body);
                AddStyles(main, font, baseSizePt);
                AddNumbering(main);

                if (PlanJson.TryGetArray(plan, "actions", out var actions))
                {
                    foreach (var action in actions.EnumerateArray())
                    {
                        ct.ThrowIfCancellationRequested();
                        var type = PlanJson.GetString(action, "type") ?? "<missing type>";
                        try
                        {
                            changes.Add(Apply(body, type, action));
                        }
                        catch (Exception ex) when (ex is not OperationCanceledException)
                        {
                            // Per-action isolation, same as the server generator.
                            changes.Add($"ERROR in {type}: {ex.Message}");
                        }
                    }
                }

                body.Append(BuildSectionProperties(settings));
                main.Document.Save();
                wordCount = CountWords(body);
            } // dispose flushes the package before sizing/moving

            File.Move(tempPath, fullPath, overwrite: true);
            return new LocalPlanExecutionResult(
                fullPath, new FileInfo(fullPath).Length, changes,
                new Dictionary<string, int> { ["word_count"] = wordCount });
        }
        catch
        {
            TryDelete(tempPath);
            throw;
        }
    }

    // ── Action handlers ─────────────────────────────────────────────────────

    private static string Apply(Body body, string type, JsonElement action) => type switch
    {
        "add_heading" => AddHeading(body, action),
        "add_paragraph" => AddParagraph(body, action),
        "add_bullet_list" => AddList(body, action, BulletNumId, "bullet"),
        "add_numbered_list" => AddList(body, action, DecimalNumId, "numbered"),
        "add_table" => AddTable(body, action),
        "add_page_break" => AddPageBreak(body),
        "add_horizontal_rule" => AddHorizontalRule(body),
        _ => throw new InvalidOperationException("unknown action"), // unreachable: CanExecute gates
    };

    private static string AddHeading(Body body, JsonElement action)
    {
        var level = Math.Clamp(PlanJson.GetInt(action, "level") ?? 1, 1, 4);
        var text = PlanJson.GetString(action, "text") ?? "";
        body.Append(new Paragraph(
            new ParagraphProperties(new ParagraphStyleId { Val = "Heading" + level }),
            NewRun(text)));
        return $"Added heading L{level}: {Truncate(text, 60)}";
    }

    private static string AddParagraph(Body body, JsonElement action)
    {
        var text = PlanJson.GetString(action, "text") ?? "";

        var runProperties = new RunProperties();
        if (PlanJson.GetBool(action, "bold"))
            runProperties.Append(new Bold());
        if (PlanJson.GetBool(action, "italic"))
            runProperties.Append(new Italic());

        var run = new Run();
        if (runProperties.HasChildren)
            run.Append(runProperties);
        run.Append(new Text(text) { Space = SpaceProcessingModeValues.Preserve });

        var paragraph = new Paragraph();
        var justification = (PlanJson.GetString(action, "alignment") ?? "left")
            .ToLowerInvariant() switch
        {
            "center" => JustificationValues.Center,
            "right" => JustificationValues.Right,
            "justify" => JustificationValues.Both,
            _ => (JustificationValues?)null,
        };
        if (justification is { } value)
            paragraph.Append(new ParagraphProperties(new Justification { Val = value }));
        paragraph.Append(run);
        body.Append(paragraph);
        return $"Added paragraph ({text.Length} chars)";
    }

    private static string AddList(Body body, JsonElement action, int numId, string kind)
    {
        var count = 0;
        if (PlanJson.TryGetArray(action, "items", out var items))
        {
            foreach (var item in items.EnumerateArray())
            {
                body.Append(new Paragraph(
                    new ParagraphProperties(new NumberingProperties(
                        new NumberingLevelReference { Val = 0 },
                        new NumberingId { Val = numId })),
                    NewRun(PlanJson.AsDisplayString(item))));
                count++;
            }
        }
        return $"Added {count} {kind} items";
    }

    private static string AddTable(Body body, JsonElement action)
    {
        var headers = PlanJson.TryGetArray(action, "headers", out var h)
            ? h.EnumerateArray().Select(PlanJson.AsDisplayString).ToList()
            : new List<string>();
        var rows = new List<List<string>>();
        if (PlanJson.TryGetArray(action, "rows", out var r))
        {
            foreach (var rowData in r.EnumerateArray())
            {
                rows.Add(rowData.ValueKind == JsonValueKind.Array
                    ? rowData.EnumerateArray().Select(PlanJson.AsDisplayString).ToList()
                    : new List<string>());
            }
        }

        var cols = headers.Count > 0 ? headers.Count : rows.FirstOrDefault()?.Count ?? 0;
        if (cols == 0)
            return "Skipped empty table";

        var professional = (PlanJson.GetString(action, "style") ?? "professional")
            .ToLowerInvariant() != "light";

        var table = new Table(new TableProperties(
            new TableBorders(
                new TopBorder { Val = BorderValues.Single, Size = 4 },
                new LeftBorder { Val = BorderValues.Single, Size = 4 },
                new BottomBorder { Val = BorderValues.Single, Size = 4 },
                new RightBorder { Val = BorderValues.Single, Size = 4 },
                new InsideHorizontalBorder { Val = BorderValues.Single, Size = 4 },
                new InsideVerticalBorder { Val = BorderValues.Single, Size = 4 }),
            new TableWidth { Type = TableWidthUnitValues.Auto, Width = "0" }));

        if (headers.Count > 0)
        {
            var headerRow = new TableRow();
            foreach (var header in headers)
            {
                var cell = new TableCell();
                if (professional)
                    cell.Append(new TableCellProperties(new Shading
                    {
                        Val = ShadingPatternValues.Clear,
                        Fill = CorporateNavyHex,
                    }));
                var runProperties = new RunProperties(new Bold());
                if (professional)
                    runProperties.Append(new Color { Val = "FFFFFF" });
                cell.Append(new Paragraph(new Run(
                    runProperties,
                    new Text(header) { Space = SpaceProcessingModeValues.Preserve })));
                headerRow.Append(cell);
            }
            table.Append(headerRow);
        }

        foreach (var rowData in rows)
        {
            var tableRow = new TableRow();
            for (var j = 0; j < cols; j++)
            {
                tableRow.Append(new TableCell(
                    new Paragraph(NewRun(j < rowData.Count ? rowData[j] : ""))));
            }
            table.Append(tableRow);
        }

        body.Append(table);
        // Word requires a paragraph between a table and whatever follows.
        body.Append(new Paragraph());
        return $"Added table: {cols} cols × {rows.Count} rows";
    }

    private static string AddPageBreak(Body body)
    {
        body.Append(new Paragraph(new Run(new Break { Type = BreakValues.Page })));
        return "Added page break";
    }

    private static string AddHorizontalRule(Body body)
    {
        body.Append(new Paragraph(new ParagraphProperties(new ParagraphBorders(
            new BottomBorder
            {
                Val = BorderValues.Single,
                Size = 6,
                Space = 1,
                Color = "CCCCCC",
            }))));
        return "Added horizontal rule";
    }

    // ── Document plumbing ───────────────────────────────────────────────────

    private static (string Font, int BaseSizePt) ResolveFont(JsonElement settings)
    {
        var thai = string.Equals(
            PlanJson.GetString(settings, "language"), "th", StringComparison.OrdinalIgnoreCase);
        var font = PlanJson.GetString(settings, "font_family")
                   ?? (thai ? "Sarabun" : "Arial");
        var size = PlanJson.GetInt(settings, "base_font_size") ?? (thai ? 16 : 12);
        return (font, size);
    }

    private static RunFonts MakeRunFonts(string font) => new()
    {
        Ascii = font,
        HighAnsi = font,
        // cs is what Thai glyphs render with — same tagging as the server's
        // setup_thai_font (finding: without it Word falls back per-script).
        ComplexScript = font,
    };

    private static void AddStyles(MainDocumentPart main, string font, int baseSizePt)
    {
        var stylesPart = main.AddNewPart<StyleDefinitionsPart>();
        var styles = new Styles(
            new DocDefaults(new RunPropertiesDefault(new RunPropertiesBaseStyle(
                MakeRunFonts(font),
                new FontSize { Val = (baseSizePt * 2).ToString() },
                new FontSizeComplexScript { Val = (baseSizePt * 2).ToString() }))),
            new Style(new StyleName { Val = "Normal" })
            {
                Type = StyleValues.Paragraph,
                StyleId = "Normal",
            });

        int[] headingSizesPt = { baseSizePt + 8, baseSizePt + 4, baseSizePt + 2, baseSizePt + 1 };
        for (var level = 1; level <= 4; level++)
        {
            var halfPoints = (headingSizesPt[level - 1] * 2).ToString();
            styles.Append(new Style(
                new StyleName { Val = "heading " + level },
                new BasedOn { Val = "Normal" },
                new StyleParagraphProperties(
                    new SpacingBetweenLines { Before = "240", After = "120" },
                    new OutlineLevel { Val = level - 1 }),
                new StyleRunProperties(
                    MakeRunFonts(font),
                    new Bold(),
                    new Color { Val = CorporateNavyHex },
                    new FontSize { Val = halfPoints },
                    new FontSizeComplexScript { Val = halfPoints }))
            {
                Type = StyleValues.Paragraph,
                StyleId = "Heading" + level,
            });
        }
        stylesPart.Styles = styles;
    }

    private static void AddNumbering(MainDocumentPart main)
    {
        var part = main.AddNewPart<NumberingDefinitionsPart>();
        part.Numbering = new Numbering(
            new AbstractNum(
                new Level(
                    new NumberingFormat { Val = NumberFormatValues.Bullet },
                    new LevelText { Val = "•" },
                    new LevelJustification { Val = LevelJustificationValues.Left },
                    new PreviousParagraphProperties(
                        new Indentation { Left = "720", Hanging = "360" }))
                { LevelIndex = 0 })
            { AbstractNumberId = BulletNumId },
            new AbstractNum(
                new Level(
                    new StartNumberingValue { Val = 1 },
                    new NumberingFormat { Val = NumberFormatValues.Decimal },
                    new LevelText { Val = "%1." },
                    new LevelJustification { Val = LevelJustificationValues.Left },
                    new PreviousParagraphProperties(
                        new Indentation { Left = "720", Hanging = "360" }))
                { LevelIndex = 0 })
            { AbstractNumberId = DecimalNumId },
            new NumberingInstance(new AbstractNumId { Val = BulletNumId })
            { NumberID = BulletNumId },
            new NumberingInstance(new AbstractNumId { Val = DecimalNumId })
            { NumberID = DecimalNumId });
    }

    private static SectionProperties BuildSectionProperties(JsonElement settings)
    {
        var landscape = string.Equals(
            PlanJson.GetString(settings, "orientation"), "landscape",
            StringComparison.OrdinalIgnoreCase);
        var letter = string.Equals(
            PlanJson.GetString(settings, "page_size"), "letter",
            StringComparison.OrdinalIgnoreCase);

        // Twips: A4 = 11906×16838, Letter = 12240×15840.
        uint w = letter ? 12240u : 11906u;
        uint h = letter ? 15840u : 16838u;
        var pageSize = landscape
            ? new PageSize { Width = h, Height = w, Orient = PageOrientationValues.Landscape }
            : new PageSize { Width = w, Height = h };

        PlanJson.TryGetObject(settings, "margins", out var margins);
        static int Twips(double inches) => (int)Math.Round(inches * 1440);
        var pageMargin = new PageMargin
        {
            Top = Twips(PlanJson.GetDouble(margins, "top") ?? 1.0),
            Bottom = Twips(PlanJson.GetDouble(margins, "bottom") ?? 1.0),
            Left = (uint)Twips(PlanJson.GetDouble(margins, "left") ?? 1.0),
            Right = (uint)Twips(PlanJson.GetDouble(margins, "right") ?? 1.0),
        };
        return new SectionProperties(pageSize, pageMargin);
    }

    private static Run NewRun(string text) =>
        new(new Text(text) { Space = SpaceProcessingModeValues.Preserve });

    private static int CountWords(Body body) =>
        body.Descendants<Text>()
            .SelectMany(t => (t.Text ?? "").Split(
                (char[]?)null, StringSplitOptions.RemoveEmptyEntries))
            .Count();

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max];

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
