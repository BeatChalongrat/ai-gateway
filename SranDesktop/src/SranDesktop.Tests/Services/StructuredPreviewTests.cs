using System.Net;
using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 16 — an office attachment opens as its STRUCTURE.
///
/// Before this, clicking an .xlsx showed the extraction pipeline's flat
/// text: a tab-separated dump ending in "showing the first part only".
/// The assertions that matter here:
///
///   * a workbook produces a grid with sheet tabs, and NOT the flat text;
///   * "load more" APPENDS and keeps the row numbers true — that is the
///     replacement for the truncation notice;
///   * every reason the structured read can fail (415, 503, offline) falls
///     back to the text, so no file becomes less viewable than it was.
/// </summary>
public sealed class StructuredPreviewRouterTests
{
    [Theory]
    [InlineData("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "a.xlsx")]
    [InlineData("application/vnd.ms-excel.sheet.macroEnabled.12", "a.xlsm")]
    [InlineData("text/csv", "rows.csv")]
    [InlineData("text/csv; charset=utf-8", "rows.csv")]
    [InlineData("text/tab-separated-values", "rows.tsv")]
    public void Spreadsheets_Get_A_Grid(string type, string name)
    {
        StructuredPreviewRouter.Resolve(type, name).Should().Be(StructuredPreviewKind.Sheet);
    }

    [Fact]
    public void Word_And_Powerpoint_Get_Their_Own_Views()
    {
        StructuredPreviewRouter.Resolve(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx")
            .Should().Be(StructuredPreviewKind.Document);
        StructuredPreviewRouter.Resolve(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation", "a.pptx")
            .Should().Be(StructuredPreviewKind.Slides);
    }

    [Theory]
    [InlineData("member_no_part_003.xlsx", StructuredPreviewKind.Sheet)]
    [InlineData("REPORT.XLSX", StructuredPreviewKind.Sheet)]
    [InlineData("notes.docx", StructuredPreviewKind.Document)]
    [InlineData("deck.pptx", StructuredPreviewKind.Slides)]
    public void A_Generic_Content_Type_Falls_Back_To_The_Extension(
        string name, StructuredPreviewKind expected)
    {
        // chat-edge stores application/octet-stream whenever the browser or
        // the OS failed to sniff a type; the filename is then all there is.
        StructuredPreviewRouter.Resolve("application/octet-stream", name).Should().Be(expected);
    }

    [Theory]
    [InlineData("application/pdf", "scan.pdf")]
    [InlineData("image/png", "shot.png")]
    [InlineData("application/zip", "bundle.zip")]
    [InlineData("text/plain", "notes.txt")]
    [InlineData(null, null)]
    public void Everything_Else_Keeps_The_Renderer_It_Already_Had(string? type, string? name)
    {
        StructuredPreviewRouter.Resolve(type, name).Should().BeNull();
    }

    [Fact]
    public void The_Phase_14_Routing_Is_Untouched()
    {
        // The structured read is layered ON TOP of the Phase 14 kinds. If it
        // is unavailable the file must still land in ExtractedText, so this
        // mapping has to stay exactly as it was.
        AttachmentPreviewRouter.Resolve("", "a.xlsx")
            .Should().Be(AttachmentPreviewKind.ExtractedText);
        AttachmentPreviewRouter.Resolve("application/pdf", "a.pdf")
            .Should().Be(AttachmentPreviewKind.Pdf);
        AttachmentPreviewRouter.Resolve("application/zip", "a.zip")
            .Should().Be(AttachmentPreviewKind.Unsupported);
    }
}

public sealed class PreviewWindowMergeTests
{
    private static SheetPreviewDto Sheet(int offset, bool hasMore, params string[] first) =>
        new(Names: new List<string> { "Sheet1" }, Index: 0, Name: "Sheet1",
            Columns: new List<string> { "A" },
            Rows: first.Select(v => new List<string> { v }).ToList(),
            Offset: offset, TotalRows: 500, HasMore: hasMore, ColumnsTruncated: false);

    [Fact]
    public void Appending_Keeps_The_Original_Offset_So_Row_Numbers_Stay_True()
    {
        var merged = PreviewWindowMerge.Append(Sheet(0, true, "a", "b"), Sheet(2, false, "c"));

        merged.Rows!.Select(r => r[0]).Should().Equal("a", "b", "c");
        // Taking the NEW window's offset would restart the numbering at the
        // start of the newest page — row "c" would be labelled 3, not 3.
        merged.Offset.Should().Be(0);
        // The metadata is the new window's: there is nothing left to fetch.
        merged.HasMore.Should().BeFalse();
    }

    [Fact]
    public void The_Next_Window_Starts_One_Past_What_Is_On_Screen()
    {
        PreviewWindowMerge.NextOffset(0, 200).Should().Be(200);
        PreviewWindowMerge.NextOffset(200, 150).Should().Be(350);
    }

    [Fact]
    public void Row_Labels_Are_One_Based_And_Offset_By_The_Window()
    {
        PreviewWindowMerge.RowLabel(0, 0).Should().Be("1");
        PreviewWindowMerge.RowLabel(0, 199).Should().Be("200");
        PreviewWindowMerge.RowLabel(200, 0).Should().Be("201");
    }

    [Fact]
    public void Merging_Tolerates_A_Window_With_No_Rows()
    {
        var merged = PreviewWindowMerge.Append(
            Sheet(0, true, "a"),
            Sheet(1, false) with { Rows = null });

        merged.Rows!.Should().HaveCount(1);
    }
}

public sealed class PreviewRowViewModelTests
{
    [Fact]
    public void A_Short_Row_Reads_Blank_Past_Its_Last_Cell()
    {
        // Trailing empty cells are stored nowhere in the file, so a row can
        // be shorter than the sheet. The grid binds one column per SHEET
        // column, and an out-of-range index during layout must not throw.
        var row = new PreviewRowViewModel("1", new[] { "a" });

        row[0].Should().Be("a");
        row[1].Should().BeEmpty();
        row[99].Should().BeEmpty();
        row[-1].Should().BeEmpty();
    }

    [Fact]
    public void A_Cell_Containing_Markup_Is_Just_A_String()
    {
        // WPF renders a bound string as text — there is no markup surface
        // here at all. This pins that the value passes through verbatim
        // rather than being parsed, stripped or escaped into something else.
        var row = new PreviewRowViewModel("1", new[] { "<img src=x onerror=alert(1)>" });

        row[0].Should().Be("<img src=x onerror=alert(1)>");
    }
}

public sealed class PreviewBlockViewModelTests
{
    private static PreviewBlockDto Block(string type, int level = 0, string? text = null,
        List<List<string>>? rows = null) => new(type, level, text, rows);

    [Fact]
    public void A_Heading_Is_Bigger_And_Bolder_The_Higher_Its_Level()
    {
        var h1 = new PreviewBlockViewModel(Block("heading", 1, "Title"));
        var h2 = new PreviewBlockViewModel(Block("heading", 2, "Section"));

        h1.IsBold.Should().BeTrue();
        h2.IsBold.Should().BeTrue();
        h1.FontSize.Should().BeGreaterThan(h2.FontSize);
        h2.FontSize.Should().BeGreaterThan(new PreviewBlockViewModel(
            Block("paragraph", 0, "body")).FontSize);
    }

    [Fact]
    public void A_Bullet_Indents_By_Its_Nesting_Level()
    {
        var top = new PreviewBlockViewModel(Block("list", 0, "one"));
        var nested = new PreviewBlockViewModel(Block("list", 2, "one.one"));

        top.IsBullet.Should().BeTrue();
        nested.Margin.Left.Should().BeGreaterThan(top.Margin.Left);
    }

    [Fact]
    public void A_Table_Block_Carries_Rows_And_No_Text()
    {
        var table = new PreviewBlockViewModel(Block("table", rows: new List<List<string>>
        {
            new() { "หัว", "ค่า" },
            new() { "A", "1" },
        }));

        table.IsTable.Should().BeTrue();
        table.IsText.Should().BeFalse();
        table.TableRows.Should().HaveCount(2);
        table.TableRows[0].Should().Equal("หัว", "ค่า");
    }

    [Fact]
    public void An_Unknown_Block_Type_Renders_As_A_Paragraph()
    {
        // The script may grow a block type before the client knows it. The
        // text must still appear rather than vanish.
        var odd = new PreviewBlockViewModel(Block("footnote", 0, "อ้างอิง"));

        odd.IsTable.Should().BeFalse();
        odd.IsText.Should().BeTrue();
        odd.Text.Should().Be("อ้างอิง");
    }
}

/// <summary>The view-model end to end, with a stubbed resolver.</summary>
public sealed class FilePreviewStructuredTests
{
    private const string XlsxType =
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

    private static SheetPreviewDto SheetPage(
        int offset, bool hasMore, string firstCell, int index = 0,
        List<string>? names = null) =>
        new(Names: names ?? new List<string> { "ยอดขาย", "Notes" },
            Index: index, Name: (names ?? new List<string> { "ยอดขาย", "Notes" })[index],
            Columns: new List<string> { "A", "B" },
            Rows: new List<List<string>> { new() { firstCell, "1" } },
            Offset: offset, TotalRows: 500, HasMore: hasMore, ColumnsTruncated: false);

    private static FilePreviewViewModel Preview(
        Func<int, int, int, Task<FilePreviewDataDto>> resolver,
        string filename = "member_no_part_003.xlsx",
        string? contentType = XlsxType,
        Func<Task<FileTextDto>>? textResolver = null) =>
        new(filename, contentType,
            () => Task.FromResult<byte[]?>(Array.Empty<byte>()),
            textResolver ?? (() => Task.FromResult(
                new FileTextDto("completed", "flat text fallback", false, false))),
            NullLogger.Instance,
            resolver);

    [Fact]
    public async Task A_Workbook_Opens_As_A_Grid_Not_As_Text()
    {
        var vm = Preview((_, _, _) => Task.FromResult(
            new FilePreviewDataDto("sheet", SheetPage(0, true, "สมชาย"), null, null)));

        await vm.LoadAsync();

        vm.IsSheet.Should().BeTrue();
        vm.IsText.Should().BeFalse("the flat dump is what Phase 16 replaces");
        vm.SheetRows.Should().HaveCount(1);
        vm.SheetRows[0][0].Should().Be("สมชาย");
        vm.SheetRows[0].Label.Should().Be("1");
        vm.SheetColumns.Should().Equal("A", "B");
        vm.SheetTabs.Select(t => t.Name).Should().Equal("ยอดขาย", "Notes");
        vm.HasSheetTabs.Should().BeTrue();
        vm.CanLoadMore.Should().BeTrue();
    }

    [Fact]
    public async Task Load_More_Appends_And_Keeps_The_Row_Numbers_True()
    {
        var offsets = new List<int>();
        var vm = Preview((_, offset, _) =>
        {
            offsets.Add(offset);
            return Task.FromResult(new FilePreviewDataDto(
                "sheet", SheetPage(offset, offset == 0, offset == 0 ? "แถวแรก" : "แถวสอง"),
                null, null));
        });

        await vm.LoadAsync();
        await vm.LoadMoreCommand.ExecuteAsync(null);

        offsets.Should().Equal(0, 1);
        vm.SheetRows.Select(r => r[0]).Should().Equal("แถวแรก", "แถวสอง");
        vm.SheetRows.Select(r => r.Label).Should().Equal("1", "2");
        vm.CanLoadMore.Should().BeFalse("the last window said there was nothing more");
    }

    [Fact]
    public async Task Switching_Sheets_Replaces_The_Rows_Rather_Than_Stacking_Them()
    {
        var requested = new List<int>();
        var vm = Preview((sheet, _, _) =>
        {
            requested.Add(sheet);
            return Task.FromResult(new FilePreviewDataDto(
                "sheet", SheetPage(0, false, sheet == 0 ? "สมชาย" : "only", sheet), null, null));
        });
        await vm.LoadAsync();

        // The command takes object? and matches, per the FIX 12 rule.
        await vm.SelectSheetCommand.ExecuteAsync(vm.SheetTabs[1]);

        requested.Should().Equal(0, 1);
        vm.SheetRows.Should().HaveCount(1);
        vm.SheetRows[0][0].Should().Be("only");
        vm.Sheet!.Index.Should().Be(1);
    }

    [Fact]
    public async Task Selecting_The_Sheet_Already_Shown_Costs_Nothing()
    {
        var calls = 0;
        var vm = Preview((sheet, _, _) =>
        {
            calls++;
            return Task.FromResult(new FilePreviewDataDto(
                "sheet", SheetPage(0, false, "สมชาย", sheet), null, null));
        });
        await vm.LoadAsync();

        await vm.SelectSheetCommand.ExecuteAsync(vm.SheetTabs[0]);

        calls.Should().Be(1, "re-selecting the open sheet must not re-fetch it");
    }

    [Fact]
    public async Task A_Command_Parameter_Of_The_Wrong_Type_Is_Ignored_Not_Fatal()
    {
        // FIX 12: a command reachable from a templated row must tolerate any
        // parameter — RelayCommand<T> THROWS on a mismatch, which is why this
        // one takes object? and matches.
        var vm = Preview((_, _, _) => Task.FromResult(
            new FilePreviewDataDto("sheet", SheetPage(0, false, "สมชาย"), null, null)));
        await vm.LoadAsync();

        var act = async () =>
        {
            vm.SelectSheetCommand.CanExecute("not a tab").Should().BeTrue();
            await vm.SelectSheetCommand.ExecuteAsync("not a tab");
            await vm.SelectSheetCommand.ExecuteAsync(null);
        };

        await act.Should().NotThrowAsync();
    }

    [Fact]
    public async Task A_Word_Document_Keeps_Its_Structure()
    {
        var vm = Preview((_, _, _) => Task.FromResult(new FilePreviewDataDto(
            "document", null,
            new DocumentPreviewDto(new List<PreviewBlockDto>
            {
                new("heading", 1, "รายงานประจำเดือน", null),
                new("paragraph", 0, "บทนำ", null),
                new("table", 0, null, new List<List<string>> { new() { "หัว", "ค่า" } }),
            }, 0, 3, false), null)),
            "report.docx", "");

        await vm.LoadAsync();

        vm.IsDocument.Should().BeTrue();
        vm.IsText.Should().BeFalse();
        vm.Blocks.Should().HaveCount(3);
        vm.Blocks[0].IsBold.Should().BeTrue();
        // The table survives AS a table — the flat text extractor moves every
        // table to the end of the document and tab-joins its cells.
        vm.Blocks[2].IsTable.Should().BeTrue();
        vm.Blocks[2].TableRows[0].Should().Equal("หัว", "ค่า");
    }

    [Fact]
    public async Task A_Deck_Comes_Back_One_Slide_At_A_Time()
    {
        var vm = Preview((_, _, _) => Task.FromResult(new FilePreviewDataDto(
            "slides", null, null,
            new SlidesPreviewDto(new List<SlidePreviewDto>
            {
                new(1, "สไลด์ 1", new List<PreviewBlockDto> { new("list", 0, "เนื้อหา 1", null) }),
                new(2, "", new List<PreviewBlockDto>()),
            }, 0, 2, false))),
            "deck.pptx", "");

        await vm.LoadAsync();

        vm.IsSlides.Should().BeTrue();
        vm.SlidePanels.Should().HaveCount(2);
        vm.SlidePanels[0].Title.Should().Be("สไลด์ 1");
        vm.SlidePanels[0].Blocks[0].Text.Should().Be("เนื้อหา 1");
        // A slide with neither a title nor text says so instead of showing
        // an empty panel.
        vm.SlidePanels[1].IsEmpty.Should().BeTrue();
    }

    [Fact]
    public async Task An_Unsupported_Type_Falls_Back_To_The_Extracted_Text()
    {
        var vm = Preview(
            (_, _, _) => throw new HttpRequestException(
                "unsupported", null, HttpStatusCode.UnsupportedMediaType));

        await vm.LoadAsync();

        vm.IsSheet.Should().BeFalse();
        vm.IsText.Should().BeTrue();
        vm.Text.Should().Be("flat text fallback");
    }

    [Fact]
    public async Task A_Deployment_Without_The_Sandbox_Falls_Back_To_The_Extracted_Text()
    {
        // 503 = the server has no structured reader. Such a deployment must
        // preview office files exactly as well as it did before Phase 16.
        var vm = Preview(
            (_, _, _) => throw new HttpRequestException(
                "unavailable", null, HttpStatusCode.ServiceUnavailable));

        await vm.LoadAsync();

        vm.IsText.Should().BeTrue();
        vm.Text.Should().Be("flat text fallback");
    }

    [Fact]
    public async Task An_Empty_Payload_Falls_Back_Instead_Of_Showing_An_Empty_Grid()
    {
        var vm = Preview((_, _, _) =>
            Task.FromResult(new FilePreviewDataDto("sheet", null, null, null)));

        await vm.LoadAsync();

        vm.IsSheet.Should().BeFalse();
        vm.IsText.Should().BeTrue();
    }

    [Fact]
    public void A_File_With_No_Server_Id_Never_Asks_For_Structure()
    {
        // No resolver at all — a locally generated file. It must degrade the
        // Phase 14 way (download it), not sit on an empty pane.
        var vm = new FilePreviewViewModel(
            "local.xlsx", XlsxType,
            () => Task.FromResult<byte[]?>(Array.Empty<byte>()),
            textResolver: null, NullLogger.Instance);

        vm.Structured.Should().BeNull();
        vm.Kind.Should().Be(AttachmentPreviewKind.Unsupported);
    }

    [Fact]
    public async Task A_Pdf_Never_Asks_For_Structure()
    {
        var asked = false;
        var vm = Preview((_, _, _) =>
        {
            asked = true;
            return Task.FromResult(new FilePreviewDataDto("sheet", null, null, null));
        }, "scan.pdf", "application/pdf");

        await vm.LoadAsync();

        asked.Should().BeFalse();
        vm.Structured.Should().BeNull();
        vm.IsPdf.Should().BeTrue();
    }

    [Fact]
    public async Task The_Summary_Says_How_Much_Of_The_Sheet_Is_On_Screen()
    {
        var vm = Preview((_, _, _) => Task.FromResult(
            new FilePreviewDataDto("sheet", SheetPage(0, true, "สมชาย"), null, null)));

        await vm.LoadAsync();

        vm.SheetSummary.Should().Contain("500");
    }

    [Fact]
    public async Task A_Retry_Does_Not_Leave_Stale_Rows_Underneath()
    {
        var fail = false;
        var vm = Preview((_, _, _) => fail
            ? throw new HttpRequestException("gone", null, HttpStatusCode.ServiceUnavailable)
            : Task.FromResult(new FilePreviewDataDto(
                "sheet", SheetPage(0, false, "สมชาย"), null, null)));

        await vm.LoadAsync();
        vm.SheetRows.Should().HaveCount(1);

        fail = true;
        await vm.RetryCommand.ExecuteAsync(null);

        vm.SheetRows.Should().BeEmpty();
        vm.IsSheet.Should().BeFalse();
        vm.IsText.Should().BeTrue();
    }
}
