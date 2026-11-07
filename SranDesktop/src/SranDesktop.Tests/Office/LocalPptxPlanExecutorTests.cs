using System.Text.Json;
using DocumentFormat.OpenXml.Packaging;
using FluentAssertions;
using SranDesktop.Infrastructure.Office;
using Xunit;
using A = DocumentFormat.OpenXml.Drawing;

namespace SranDesktop.Tests.Office;

/// <summary>Phase 16 B: on-device :::pptx-plan (pptx) execution via
/// DocumentFormat.OpenXml PresentationDocument — same plan JSON as
/// scripts/pptx_generator.py (non-chart slide types, navy 0A1230 palette).</summary>
public class LocalPptxPlanExecutorTests : IDisposable
{
    private readonly string _dir;
    private readonly LocalPptxPlanExecutor _executor = new();

    public LocalPptxPlanExecutorTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), $"sran-pptx-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_dir);
    }

    public void Dispose()
    {
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    private static JsonElement Plan(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }

    private const string RepresentativeDeck = """
    {
      "presentation_settings": {"language": "en", "title": "Deck", "author": "Ada"},
      "slides": [
        {"type": "title", "title": "Quarterly Review", "subtitle": "FY26", "author": "Ada"},
        {"type": "section_divider", "title": "Results"},
        {"type": "content", "title": "Highlights", "body": "A strong quarter.",
         "bullets": ["Revenue up", "Churn down"]},
        {"type": "stats", "title": "KPIs",
         "stats": [{"value": "99.9%", "label": "Uptime"}, {"value": "42", "label": "NPS"}]},
        {"type": "table", "title": "Numbers",
         "headers": ["Metric", "Value"], "rows": [["ARR", "$1.2M"]]},
        {"type": "closing", "title": "Thank You", "subtitle": "Questions?"}
      ]
    }
    """;

    [Fact]
    public async Task Representative_Deck_Produces_Valid_Reopenable_Pptx()
    {
        var output = Path.Combine(_dir, "deck.pptx");

        var result = await _executor.ExecuteAsync(Plan(RepresentativeDeck), output);

        result.FileSizeBytes.Should().BeGreaterThan(0);
        result.Changes.Should().NotContain(c => c.StartsWith("ERROR"));
        result.Changes.Should().Contain("Title: Quarterly Review");
        result.Changes.Should().Contain("Table: Numbers (2 cols × 1 rows)");
        result.Changes.Should().Contain("Stats: KPIs (2 KPIs)");
        result.Stats["slides"].Should().Be(6);
        result.Stats["charts"].Should().Be(0);

        // Reopen with the OpenXml SDK and verify structure + content.
        using var doc = PresentationDocument.Open(output, isEditable: false);
        var presentationPart = doc.PresentationPart!;
        presentationPart.SlideParts.Should().HaveCount(6);

        var allText = presentationPart.SlideParts
            .SelectMany(sp => sp.Slide!.Descendants<A.Text>())
            .Select(t => t.Text)
            .ToList();
        allText.Should().Contain("Quarterly Review");
        allText.Should().Contain("Results");
        allText.Should().Contain(t => t.Contains("Revenue up")); // bulleted
        allText.Should().Contain("Uptime");
        allText.Should().Contain("$1.2M");
        allText.Should().Contain("Thank You");

        // A table graphic-frame is present with header + data cells.
        var tables = presentationPart.SlideParts
            .SelectMany(sp => sp.Slide!.Descendants<A.Table>()).ToList();
        tables.Should().ContainSingle();
        var cellText = tables[0].Descendants<A.Text>().Select(t => t.Text).ToList();
        cellText.Should().Contain("Metric").And.Contain("ARR").And.Contain("$1.2M");
    }

    [Fact]
    public async Task Palette_And_Layout_Parity_SpotCheck()
    {
        var output = Path.Combine(_dir, "palette.pptx");
        await _executor.ExecuteAsync(Plan(RepresentativeDeck), output);

        using var doc = PresentationDocument.Open(output, isEditable: false);
        var presentationPart = doc.PresentationPart!;

        // Default (16:9) layout: 10in × 5.625in in EMU.
        var size = presentationPart.Presentation!.SlideSize!;
        size.Cx!.Value.Should().Be(9144000);
        size.Cy!.Value.Should().Be(5143500);

        // The title slide background is corporate navy 0A1230 (primary).
        var titleSlide = presentationPart.SlideParts.First();
        var bgHex = titleSlide.Slide!.CommonSlideData!.Background!
            .Descendants<A.RgbColorModelHex>().First().Val!.Value;
        bgHex.Should().Be("0A1230");
    }

    [Fact]
    public async Task Color_Scheme_Override_Is_Applied()
    {
        var output = Path.Combine(_dir, "override.pptx");
        await _executor.ExecuteAsync(Plan("""
        {
          "presentation_settings": {"color_scheme": {"primary": "112233"}},
          "slides": [{"type": "title", "title": "X"}]
        }
        """), output);

        using var doc = PresentationDocument.Open(output, isEditable: false);
        var bgHex = doc.PresentationPart!.SlideParts.First().Slide!.CommonSlideData!.Background!
            .Descendants<A.RgbColorModelHex>().First().Val!.Value;
        bgHex.Should().Be("112233");
    }

    [Fact]
    public async Task Thai_Settings_Apply_Sarabun_Typeface()
    {
        var output = Path.Combine(_dir, "thai.pptx");
        await _executor.ExecuteAsync(Plan("""
        {
          "presentation_settings": {"language": "th"},
          "slides": [{"type": "content", "title": "รายงาน", "bullets": ["ข้อมูล"]}]
        }
        """), output);

        using var doc = PresentationDocument.Open(output, isEditable: false);
        var fonts = doc.PresentationPart!.SlideParts.First().Slide!
            .Descendants<A.LatinFont>().Select(f => f.Typeface!.Value).ToList();
        fonts.Should().OnlyContain(f => f == "Sarabun");
    }

    [Fact]
    public async Task Layout_4x3_Sets_Taller_Slide()
    {
        var output = Path.Combine(_dir, "43.pptx");
        await _executor.ExecuteAsync(Plan("""
        {
          "presentation_settings": {"layout": "4:3"},
          "slides": [{"type": "title", "title": "X"}]
        }
        """), output);

        using var doc = PresentationDocument.Open(output, isEditable: false);
        var size = doc.PresentationPart!.Presentation!.SlideSize!;
        size.Cx!.Value.Should().Be(9144000);   // 10in
        size.Cy!.Value.Should().Be(6858000);   // 7.5in
    }

    [Fact]
    public void CanExecute_True_For_Supported_Deck()
    {
        _executor.CanExecute(Plan(RepresentativeDeck)).Should().BeTrue();
    }

    [Theory]
    // a chart slide embeds a spreadsheet part the local path skips → server.
    [InlineData("""{"slides":[{"type":"title","title":"X"},{"type":"chart","title":"Y","chart_type":"bar"}]}""")]
    // editing an uploaded deck needs the server's copy of the source.
    [InlineData("""{"source_file_id":"f-1","slides":[{"type":"title","title":"X"}]}""")]
    // an unknown/future slide type stays server-side.
    [InlineData("""{"slides":[{"type":"timeline","title":"X"}]}""")]
    public void CanExecute_False_For_Unsupported_Plans(string json)
    {
        _executor.CanExecute(Plan(json)).Should().BeFalse();
    }

    [Fact]
    public void Chart_Deck_Is_Never_Executed_Locally()
    {
        // Belt-and-braces: even if asked, a chart deck must not be produced
        // on-device (CanExecute already gates this in PlanBlockViewModel).
        var chart = Plan("""{"slides":[{"type":"chart","title":"Y","chart_type":"bar"}]}""");
        _executor.CanExecute(chart).Should().BeFalse();
    }
}
