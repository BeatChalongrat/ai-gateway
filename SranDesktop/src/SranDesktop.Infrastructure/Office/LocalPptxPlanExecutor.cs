using System.Text;
using System.Text.Json;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using A = DocumentFormat.OpenXml.Drawing;
using P = DocumentFormat.OpenXml.Presentation;

namespace SranDesktop.Infrastructure.Office;

/// <summary>
/// On-device :::pptx-plan executor (Phase 16 B) for format=pptx, interpreting
/// the same plan JSON as the server's scripts/pptx_generator.py through
/// DocumentFormat.OpenXml (MIT) — no new package (v3's PresentationDocument was
/// already on the graph for docx).
///
/// Supported locally (CanExecute true): create-mode decks whose every slide is
/// one of title / section_divider / content / two_column / stats / table /
/// closing / content_with_image. Palette + layout mirror pptx_generator.py
/// (corporate navy 0A1230, blank 16:9 by default, the same inch positions).
///
/// NOT supported (CanExecute false → transparent chat-edge fallback):
/// - any "chart" slide (charts embed a spreadsheet part the local path skips),
/// - any other/unknown slide type (future template kinds stay server-side),
/// - source_file_id / edit-mode decks (the uploaded source lives server-side).
///
/// Fidelity gap vs. the server: speaker_notes are dropped locally (a notes
/// master/part backbone isn't built) — the slide content is otherwise identical.
///
/// Privacy: like the excel/doc executors, generation stays on-device; the plan
/// (already delivered through the masked pipeline) never transits chat-edge.
/// </summary>
public sealed class LocalPptxPlanExecutor : ILocalPlanExecutor
{
    private const long Emu = 914400; // EMU per inch

    /// <summary>Slide kinds the local path reproduces with full fidelity.
    /// Anything else — notably "chart" — forces the server fallback.</summary>
    private static readonly HashSet<string> SupportedSlideTypes = new(StringComparer.Ordinal)
    {
        "title", "section_divider", "content", "two_column",
        "stats", "table", "closing", "content_with_image",
    };

    public string PlanType => "pptx";

    public bool CanExecute(JsonElement plan)
    {
        if (plan.ValueKind != JsonValueKind.Object)
            return false;
        // Edit mode appends to the uploaded source, which only the server has.
        if (!string.IsNullOrEmpty(PlanJson.GetString(plan, "source_file_id")))
            return false;

        if (PlanJson.TryGetArray(plan, "slides", out var slides))
        {
            foreach (var slide in slides.EnumerateArray())
            {
                if (slide.ValueKind != JsonValueKind.Object)
                    return false;
                // Mirror pptx_generator's lower-casing of the slide type.
                var type = (PlanJson.GetString(slide, "type") ?? "content").ToLowerInvariant();
                if (!SupportedSlideTypes.Contains(type))
                    return false; // chart / unknown → server
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

        // Temp sibling + move-on-success: never leave a corrupt .pptx behind.
        var tempPath = Path.Combine(
            Path.GetDirectoryName(fullPath)!,
            $"{Path.GetFileNameWithoutExtension(fullPath)}.{Guid.NewGuid():N}.tmp{Path.GetExtension(fullPath)}");
        try
        {
            var settings = ResolveSettings(plan);
            var colors = ResolveColors(plan);

            var changes = new List<string>();
            var okCount = 0;
            var slideCount = 0;

            using (var doc = PresentationDocument.Create(tempPath, PresentationDocumentType.Presentation))
            {
                var presentationPart = doc.AddPresentationPart();
                var (layoutPart, sldIdList) = PptxBackbone.Build(
                    presentationPart, settings.SlideWidth, settings.SlideHeight);

                if (PlanJson.TryGetArray(plan, "slides", out var slides))
                {
                    var index = 0;
                    foreach (var slide in slides.EnumerateArray())
                    {
                        ct.ThrowIfCancellationRequested();
                        var type = (PlanJson.GetString(slide, "type") ?? "content").ToLowerInvariant();
                        try
                        {
                            var tree = new ShapeTreeBuilder();
                            var detail = BuildSlide(type, slide, settings, colors, tree);

                            var slidePart = presentationPart.AddNewPart<SlidePart>();
                            slidePart.Slide = tree.ToSlide();
                            slidePart.AddPart(layoutPart, "rId1");
                            var relId = presentationPart.GetIdOfPart(slidePart);
                            sldIdList.Append(new P.SlideId
                            {
                                Id = 256U + (uint)index,
                                RelationshipId = relId,
                            });
                            changes.Add(detail);
                            okCount++;
                            slideCount++;
                        }
                        catch (Exception ex) when (ex is not OperationCanceledException)
                        {
                            // Per-slide isolation, same as the server generator.
                            changes.Add($"ERROR in {type}: {ex.Message}");
                        }
                        index++;
                    }
                }

                if (slideCount == 0)
                    throw new InvalidOperationException("plan produced a deck with no slides");

                presentationPart.Presentation.Save();
            }

            File.Move(tempPath, fullPath, overwrite: true);
            return new LocalPlanExecutionResult(
                fullPath, new FileInfo(fullPath).Length, changes,
                new Dictionary<string, int>
                {
                    ["slides"] = slideCount,
                    ["charts"] = 0, // charts are never generated locally (they force fallback)
                });
        }
        catch
        {
            TryDelete(tempPath);
            throw;
        }
    }

    // ── Slide dispatch ──────────────────────────────────────────────────────

    private static string BuildSlide(
        string type, JsonElement data, DeckSettings settings, Palette colors, ShapeTreeBuilder tree)
        => type switch
        {
            "title" => BuildTitle(data, settings, colors, tree),
            "section_divider" => BuildSectionDivider(data, settings, colors, tree),
            "two_column" => BuildTwoColumn(data, settings, colors, tree),
            "stats" => BuildStats(data, settings, colors, tree),
            "table" => BuildTable(data, settings, colors, tree),
            "closing" => BuildClosing(data, settings, colors, tree),
            // content + content_with_image + (unreachable) unknown → content.
            _ => BuildContent(data, settings, colors, tree),
        };

    private static string BuildTitle(
        JsonElement data, DeckSettings s, Palette c, ShapeTreeBuilder t)
    {
        t.Background(c.Primary);
        var title = PlanJson.GetString(data, "title") ?? "";
        t.TextBox(1, 1.5, 8, 1.5, title, s.HeadingFont, 40, true, c.TextLight, A.TextAlignmentTypeValues.Left);

        var subtitle = PlanJson.GetString(data, "subtitle") ?? "";
        if (subtitle.Length > 0)
            t.TextBox(1, 3.0, 8, 0.8, subtitle, s.BodyFont, 20, false, "B0C4DE", A.TextAlignmentTypeValues.Left);

        var author = PlanJson.GetString(data, "author") ?? "";
        if (author.Length > 0)
            t.TextBox(1, 4.0, 8, 0.5, author, s.BodyFont, 14, false, "8899AA", A.TextAlignmentTypeValues.Left);

        // Accent bar (thin filled rectangle).
        t.Rectangle(1, 2.9, 2, 0.04, c.Accent);
        return $"Title: {Cut(title, 60)}";
    }

    private static string BuildSectionDivider(
        JsonElement data, DeckSettings s, Palette c, ShapeTreeBuilder t)
    {
        t.Background(c.Secondary);
        var title = PlanJson.GetString(data, "title") ?? "";
        t.TextBox(1, 2.0, 8, 1.5, title, s.HeadingFont, 36, true, c.TextLight, A.TextAlignmentTypeValues.Center);
        return $"Section: {Cut(title, 60)}";
    }

    private static string BuildContent(
        JsonElement data, DeckSettings s, Palette c, ShapeTreeBuilder t)
    {
        t.Background(c.LightBg);
        var title = PlanJson.GetString(data, "title") ?? "";
        t.TextBox(0.7, 0.4, 8.6, 0.7, title, s.HeadingFont, s.HeadingSize, true, c.TextDark);

        var y = 1.3;
        var body = PlanJson.GetString(data, "body") ?? "";
        if (body.Length > 0)
        {
            t.TextBox(0.7, y, 8.6, 1.0, body, s.BodyFont, s.BodySize, false, c.TextDark);
            y += 1.0;
        }

        var bullets = ReadStrings(data, "bullets");
        if (bullets.Count > 0)
            t.Bullets(0.7, y, 8.6, Math.Max(3.8 - (y - 1.3), 1.0), bullets, s.BodyFont, s.BodySize, c.TextDark);

        var parts = new List<string> { $"Content: {Cut(title, 50)}" };
        if (bullets.Count > 0)
            parts.Add($"{bullets.Count} bullets");
        return string.Join(" — ", parts);
    }

    private static string BuildTwoColumn(
        JsonElement data, DeckSettings s, Palette c, ShapeTreeBuilder t)
    {
        t.Background(c.LightBg);
        var title = PlanJson.GetString(data, "title") ?? "";
        t.TextBox(0.7, 0.4, 8.6, 0.7, title, s.HeadingFont, s.HeadingSize, true, c.TextDark);

        void Column(double x, JsonElement col)
        {
            t.Rectangle(x, 1.4, 4.3, 3.6, "FFFFFF");
            var heading = PlanJson.GetString(col, "heading") ?? "";
            t.TextBox(x + 0.3, 1.6, 3.7, 0.5, heading, s.HeadingFont, 20, true, c.Accent);
            var bullets = ReadStrings(col, "bullets");
            if (bullets.Count > 0)
                t.Bullets(x + 0.3, 2.2, 3.7, 2.5, bullets, s.BodyFont, s.BodySize, c.TextDark);
        }

        Column(0.5, PlanJson.TryGetObject(data, "left", out var left) ? left : default);
        Column(5.2, PlanJson.TryGetObject(data, "right", out var right) ? right : default);
        return $"Two-column: {Cut(title, 50)}";
    }

    private static string BuildStats(
        JsonElement data, DeckSettings s, Palette c, ShapeTreeBuilder t)
    {
        t.Background(c.LightBg);
        var title = PlanJson.GetString(data, "title") ?? "";
        t.TextBox(0.7, 0.4, 8.6, 0.7, title, s.HeadingFont, s.HeadingSize, true, c.TextDark);

        var stats = PlanJson.TryGetArray(data, "stats", out var arr)
            ? arr.EnumerateArray().Where(e => e.ValueKind == JsonValueKind.Object).ToList()
            : new List<JsonElement>();
        if (stats.Count == 0)
            return $"Stats: {Cut(title, 50)} (empty)";

        var num = Math.Min(stats.Count, 4);
        const double totalWidth = 9.0;
        const double gap = 0.3;
        var cardW = (totalWidth - gap * (num - 1)) / num;
        const double startX = 0.5;

        for (var i = 0; i < num; i++)
        {
            var stat = stats[i];
            var x = startX + i * (cardW + gap);
            t.Rectangle(x, 1.6, cardW, 3.0, "FFFFFF");
            var accent = PlanJson.GetString(stat, "color") ?? c.Accent;
            t.Rectangle(x, 1.6, cardW, 0.06, accent);
            var value = ScalarString(stat, "value");
            t.TextBox(x + 0.2, 2.0, cardW - 0.4, 1.2, value, s.HeadingFont, 40, true, accent, A.TextAlignmentTypeValues.Center);
            var label = PlanJson.GetString(stat, "label") ?? "";
            t.TextBox(x + 0.2, 3.2, cardW - 0.4, 0.8, label, s.BodyFont, 14, false, "6B7280", A.TextAlignmentTypeValues.Center);
        }
        return $"Stats: {Cut(title, 50)} ({num} KPI{(num != 1 ? "s" : "")})";
    }

    private static string BuildTable(
        JsonElement data, DeckSettings s, Palette c, ShapeTreeBuilder t)
    {
        t.Background(c.LightBg);
        var title = PlanJson.GetString(data, "title") ?? "";
        t.TextBox(0.7, 0.4, 8.6, 0.7, title, s.HeadingFont, s.HeadingSize, true, c.TextDark);

        var headers = ReadStrings(data, "headers");
        var rows = new List<List<string>>();
        if (PlanJson.TryGetArray(data, "rows", out var rowsArr))
        {
            foreach (var row in rowsArr.EnumerateArray())
                rows.Add(row.ValueKind == JsonValueKind.Array
                    ? row.EnumerateArray().Select(PlanJson.AsDisplayString).ToList()
                    : new List<string>());
        }
        if (headers.Count == 0 && rows.Count == 0)
            return $"Table: {Cut(title, 50)} (empty)";

        var numCols = headers.Count > 0 ? headers.Count : rows[0].Count;
        var numRows = (headers.Count > 0 ? 1 : 0) + rows.Count;
        var height = Math.Min(numRows * 0.45, 3.8);

        t.Table(0.5, 1.3, 9.0, height, headers, rows, numCols, s.BodyFont,
            c.Primary, c.LightBg, c.TextDark, c.TextLight);
        return $"Table: {Cut(title, 50)} ({numCols} cols × {rows.Count} rows)";
    }

    private static string BuildClosing(
        JsonElement data, DeckSettings s, Palette c, ShapeTreeBuilder t)
    {
        t.Background(c.Primary);
        var title = PlanJson.GetString(data, "title") ?? "Thank You";
        t.TextBox(1, 2.0, 8, 1.2, title, s.HeadingFont, 40, true, c.TextLight, A.TextAlignmentTypeValues.Center);
        var subtitle = PlanJson.GetString(data, "subtitle") ?? "";
        if (subtitle.Length > 0)
            t.TextBox(1, 3.4, 8, 0.6, subtitle, s.BodyFont, 18, false, "B0C4DE", A.TextAlignmentTypeValues.Center);
        return $"Closing: {Cut(title, 50)}";
    }

    // ── Settings / palette ──────────────────────────────────────────────────

    private sealed record DeckSettings(
        string HeadingFont, string BodyFont, int HeadingSize, int BodySize,
        long SlideWidth, long SlideHeight, string? Title, string? Author);

    private static DeckSettings ResolveSettings(JsonElement plan)
    {
        PlanJson.TryGetObject(plan, "presentation_settings", out var settings);
        PlanJson.TryGetObject(settings, "font", out var font);

        var language = (PlanJson.GetString(settings, "language") ?? "en").ToLowerInvariant();
        var thai = language == "th";
        var headingFont = PlanJson.GetString(font, "heading") ?? (thai ? "Sarabun" : "Calibri");
        var bodyFont = PlanJson.GetString(font, "body") ?? (thai ? "Sarabun" : "Calibri");
        var headingSize = PlanJson.GetInt(font, "heading_size") ?? (thai ? 32 : 28);
        var bodySize = PlanJson.GetInt(font, "body_size") ?? (thai ? 18 : 16);

        // Width is always 10in; height follows the layout aspect (default 16:9).
        var layout = PlanJson.GetString(settings, "layout") ?? "16:9";
        var height = layout switch
        {
            "4:3" => 7.5,
            "16:10" => 6.25,
            _ => 5.625,
        };
        return new DeckSettings(
            headingFont, bodyFont, headingSize, bodySize,
            (long)(10 * Emu), (long)(height * Emu),
            PlanJson.GetString(settings, "title"), PlanJson.GetString(settings, "author"));
    }

    private sealed record Palette(
        string Primary, string Secondary, string Accent, string LightBg,
        string TextDark, string TextLight);

    private static Palette ResolveColors(JsonElement plan)
    {
        // Defaults mirror pptx_generator.DEFAULT_COLORS.
        var map = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            ["primary"] = "0A1230",
            ["secondary"] = "0F1835",
            ["accent"] = "2E75B6",
            ["light_bg"] = "F6F7F9",
            ["text_dark"] = "1A1A2E",
            ["text_light"] = "FFFFFF",
        };
        PlanJson.TryGetObject(plan, "presentation_settings", out var settings);
        if (PlanJson.TryGetObject(settings, "color_scheme", out var scheme))
        {
            foreach (var entry in scheme.EnumerateObject())
                if (entry.Value.ValueKind == JsonValueKind.String && entry.Value.GetString() is { Length: > 0 } v)
                    map[entry.Name] = v;
        }
        return new Palette(
            map["primary"], map["secondary"], map["accent"],
            map["light_bg"], map["text_dark"], map["text_light"]);
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private static List<string> ReadStrings(JsonElement obj, string property) =>
        PlanJson.TryGetArray(obj, property, out var arr)
            ? arr.EnumerateArray().Select(PlanJson.AsDisplayString).Where(s => s.Length > 0).ToList()
            : new List<string>();

    private static string ScalarString(JsonElement obj, string property) =>
        obj.ValueKind == JsonValueKind.Object && obj.TryGetProperty(property, out var v)
            ? PlanJson.AsDisplayString(v)
            : "";

    private static string Cut(string value, int max) =>
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

    // ── Package backbone (master / layout / theme / presentation) ────────────

    /// <summary>
    /// Build the minimal, valid four-part backbone every .pptx needs and return
    /// the shared blank layout each slide references + the slide size in EMU.
    /// Master / layout / theme are static boilerplate written as raw XML; the
    /// presentation's id lists + slide size are set on the typed DOM.
    /// </summary>
}
