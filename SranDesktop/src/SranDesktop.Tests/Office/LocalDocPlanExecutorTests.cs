using System.Text.Json;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;
using FluentAssertions;
using SranDesktop.Infrastructure.Office;
using Xunit;

namespace SranDesktop.Tests.Office;

/// <summary>Phase 12 E: on-device :::doc-plan (docx) execution via
/// DocumentFormat.OpenXml — same plan JSON as scripts/docx_generator.py.</summary>
public class LocalDocPlanExecutorTests : IDisposable
{
    private readonly string _dir;
    private readonly LocalDocPlanExecutor _executor = new();

    public LocalDocPlanExecutorTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), $"sran-docx-{Guid.NewGuid():N}");
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

    private const string RepresentativePlan = """
    {
      "format": "docx",
      "output_filename": "summary.docx",
      "document_settings": {"language": "en", "page_size": "A4", "orientation": "portrait"},
      "actions": [
        {"type": "add_heading", "level": 1, "text": "Quarterly Report"},
        {"type": "add_paragraph", "text": "Revenue grew steadily.", "alignment": "left"},
        {"type": "add_heading", "level": 2, "text": "Highlights"},
        {"type": "add_bullet_list", "items": ["Alpha shipped", "Beta launched"]},
        {"type": "add_numbered_list", "items": ["First", "Second"]},
        {"type": "add_table", "headers": ["KPI", "Value"], "rows": [["Uptime", "99.9%"]]},
        {"type": "add_page_break"},
        {"type": "add_horizontal_rule"}
      ]
    }
    """;

    [Fact]
    public async Task Representative_Plan_Produces_Valid_Docx()
    {
        var output = Path.Combine(_dir, "summary.docx");

        var result = await _executor.ExecuteAsync(Plan(RepresentativePlan), output);

        result.FileSizeBytes.Should().BeGreaterThan(0);
        result.Changes.Should().NotContain(c => c.StartsWith("ERROR"));
        result.Changes.Should().Contain("Added heading L1: Quarterly Report");
        result.Changes.Should().Contain("Added table: 2 cols × 1 rows");
        result.Stats["word_count"].Should().BeGreaterThan(5);

        // Reopen with the OpenXml SDK and verify structure.
        using var doc = WordprocessingDocument.Open(output, isEditable: false);
        var body = doc.MainDocumentPart!.Document!.Body!;
        var paragraphs = body.Elements<Paragraph>().ToList();

        var h1 = paragraphs.First(p =>
            p.ParagraphProperties?.ParagraphStyleId?.Val == "Heading1");
        h1.InnerText.Should().Be("Quarterly Report");
        paragraphs.Should().Contain(p =>
            p.ParagraphProperties != null
            && p.ParagraphProperties.ParagraphStyleId != null
            && p.ParagraphProperties.ParagraphStyleId.Val == "Heading2");
        body.InnerText.Should().Contain("Revenue grew steadily.");

        // Bullet + numbered items carry numbering properties.
        var numbered = paragraphs
            .Where(p => p.ParagraphProperties?.NumberingProperties is not null)
            .ToList();
        numbered.Should().HaveCount(4);
        numbered.Select(p => p.InnerText).Should()
            .Contain(new[] { "Alpha shipped", "Beta launched", "First", "Second" });

        // Table: header row + one data row.
        var table = body.Elements<Table>().Single();
        table.Elements<TableRow>().Should().HaveCount(2);
        table.InnerText.Should().Contain("KPI").And.Contain("99.9%");
    }

    [Fact]
    public async Task Thai_Settings_Apply_Sarabun_With_ComplexScript_Tagging()
    {
        var plan = Plan("""
        {
          "format": "docx",
          "document_settings": {"language": "th"},
          "actions": [{"type": "add_paragraph", "text": "สวัสดี"}]
        }
        """);
        var output = Path.Combine(_dir, "thai.docx");

        await _executor.ExecuteAsync(plan, output);

        using var doc = WordprocessingDocument.Open(output, isEditable: false);
        var defaults = doc.MainDocumentPart!.StyleDefinitionsPart!.Styles!
            .GetFirstChild<DocDefaults>()!;
        var fonts = defaults.RunPropertiesDefault!.RunPropertiesBaseStyle!
            .GetFirstChild<RunFonts>()!;
        fonts.Ascii!.Value.Should().Be("Sarabun");
        // cs is what Thai glyphs render with — the server's setup_thai_font
        // exists exactly because this tag is easy to miss.
        fonts.ComplexScript!.Value.Should().Be("Sarabun");
    }

    [Fact]
    public void CanExecute_True_For_Supported_Docx_Plan()
    {
        _executor.CanExecute(Plan(RepresentativePlan)).Should().BeTrue();
    }

    [Theory]
    // pdf stays server-side (reportlab + bundled Thai fonts).
    [InlineData("""{"format":"pdf","actions":[{"type":"add_paragraph","text":"x"}]}""")]
    // editing an uploaded document needs the server's copy of the source.
    [InlineData("""{"format":"docx","source_file_id":"f-1","actions":[{"type":"add_paragraph","text":"x"}]}""")]
    // replace_text only makes sense against a source document.
    [InlineData("""{"format":"docx","actions":[{"type":"replace_text","find":"a","replace":"b"}]}""")]
    // header/footer parts ({page} field codes) are not built locally.
    [InlineData("""{"format":"docx","actions":[{"type":"set_footer","text":"Page {page}"}]}""")]
    [InlineData("""{"format":"docx","document_settings":{"footer_text":"Page {page} of {total}"},"actions":[{"type":"add_paragraph","text":"x"}]}""")]
    // unknown action type → server.
    [InlineData("""{"format":"docx","actions":[{"type":"add_toc"}]}""")]
    public void CanExecute_False_For_Unsupported_Plans(string json)
    {
        _executor.CanExecute(Plan(json)).Should().BeFalse();
    }
}
