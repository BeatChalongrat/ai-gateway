using System.Text.Json;
using ClosedXML.Excel;
using FluentAssertions;
using SranDesktop.Infrastructure.Office;
using Xunit;

namespace SranDesktop.Tests.Office;

/// <summary>Phase 12 E: on-device :::excel-plan execution via ClosedXML —
/// same plan JSON as the server's scripts/excel_executor.py.</summary>
public class LocalExcelPlanExecutorTests : IDisposable
{
    private readonly string _dir;
    private readonly LocalExcelPlanExecutor _executor = new();

    public LocalExcelPlanExecutorTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), $"sran-xlsx-{Guid.NewGuid():N}");
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
      "operation": "create_workbook",
      "output_filename": "report.xlsx",
      "actions": [
        {"type": "create_sheet", "sheet_name": "Data"},
        {"type": "write_table", "sheet_name": "Data", "start_cell": "A1",
         "headers": ["Month", "Sales"],
         "data": [["Jan", 100], ["Feb", 250.5]]},
        {"type": "write_cells", "sheet_name": "Data", "cells": [
           {"cell": "A4", "value": "Total", "bold": true},
           {"cell": "B4", "value_formula": "=SUM(B2:B3)", "number_format": "#,##0.00"}]},
        {"type": "format_range", "sheet_name": "Data", "range": "A1:B1",
         "fill_color": "0A1230", "font_color": "FFFFFF", "bold": true},
        {"type": "set_column_width", "sheet_name": "Data", "columns": {"A": 18, "B": 14}},
        {"type": "merge_cells", "sheet_name": "Data", "range": "A6:B6"},
        {"type": "auto_filter", "sheet_name": "Data", "range": "A1:B3"},
        {"type": "freeze_panes", "sheet_name": "Data", "cell": "A2"}
      ]
    }
    """;

    [Fact]
    public async Task Representative_Plan_Produces_Valid_Workbook()
    {
        var output = Path.Combine(_dir, "report.xlsx");

        var result = await _executor.ExecuteAsync(Plan(RepresentativePlan), output);

        result.OutputPath.Should().Be(Path.GetFullPath(output));
        result.FileSizeBytes.Should().BeGreaterThan(0);
        result.Changes.Should().NotContain(c => c.StartsWith("ERROR"));
        result.Stats["sheets"].Should().Be(1);

        // Reopen with ClosedXML and verify content survived the round trip.
        using var wb = new XLWorkbook(output);
        var ws = wb.Worksheet("Data");
        ws.Cell("A1").GetString().Should().Be("Month");
        ws.Cell("A1").Style.Font.Bold.Should().BeTrue();
        ws.Cell("B2").GetDouble().Should().Be(100);
        ws.Cell("B3").GetDouble().Should().Be(250.5);
        ws.Cell("A4").GetString().Should().Be("Total");
        ws.Cell("B4").FormulaA1.Should().Contain("SUM(B2:B3)");
        ws.Cell("B4").Style.NumberFormat.Format.Should().Be("#,##0.00");
        ws.Column("A").Width.Should().BeApproximately(18, 0.01);
        ws.MergedRanges.Count.Should().Be(1);
        ws.AutoFilter.IsEnabled.Should().BeTrue();
    }

    [Fact]
    public void CanExecute_True_For_Supported_Create_Workbook_Plan()
    {
        _executor.CanExecute(Plan(RepresentativePlan)).Should().BeTrue();
    }

    [Theory]
    // create_chart is not reproducible with ClosedXML → server.
    [InlineData("""{"operation":"create_workbook","actions":[{"type":"create_sheet","sheet_name":"S"},{"type":"create_chart","sheet_name":"S","chart_type":"bar"}]}""")]
    // modify_workbook needs the uploaded source file → server.
    [InlineData("""{"operation":"modify_workbook","actions":[{"type":"write_cells","sheet_name":"S","cells":[]}]}""")]
    // operation omitted = server default modify_workbook → server.
    [InlineData("""{"actions":[{"type":"create_sheet","sheet_name":"S"}]}""")]
    // source_file_id always goes to the server.
    [InlineData("""{"operation":"create_workbook","source_file_id":"f-1","actions":[{"type":"create_sheet","sheet_name":"S"}]}""")]
    // unknown action type → server (it logs the ERROR consistently).
    [InlineData("""{"operation":"create_workbook","actions":[{"type":"create_sheet","sheet_name":"S"},{"type":"pivot_table","sheet_name":"S"}]}""")]
    // no actions array at all.
    [InlineData("""{"operation":"create_workbook"}""")]
    public void CanExecute_False_For_Unsupported_Plans(string json)
    {
        _executor.CanExecute(Plan(json)).Should().BeFalse();
    }

    [Fact]
    public async Task Bad_Action_Is_Recorded_As_Error_Without_Aborting()
    {
        // write_cells targets a sheet that doesn't exist — the server executor
        // records "ERROR in write_cells: …" and keeps going; ours must match.
        var plan = Plan("""
        {
          "operation": "create_workbook",
          "actions": [
            {"type": "create_sheet", "sheet_name": "Real"},
            {"type": "write_cells", "sheet_name": "Missing",
             "cells": [{"cell": "A1", "value": "x"}]},
            {"type": "write_cells", "sheet_name": "Real",
             "cells": [{"cell": "A1", "value": "kept"}]}
          ]
        }
        """);
        var output = Path.Combine(_dir, "partial.xlsx");

        var result = await _executor.ExecuteAsync(plan, output);

        result.Changes.Should().HaveCount(3);
        result.Changes[1].Should().StartWith("ERROR in write_cells:");
        using var wb = new XLWorkbook(output);
        wb.Worksheet("Real").Cell("A1").GetString().Should().Be("kept");
    }

    [Fact]
    public async Task MidExecution_Failure_Leaves_No_File_Behind()
    {
        // The only create_sheet has an illegal name → the action errors, the
        // workbook ends up empty, and saving must throw WITHOUT leaving a
        // partial/corrupt file at (or beside) the output path.
        var plan = Plan("""
        {
          "operation": "create_workbook",
          "actions": [{"type": "create_sheet", "sheet_name": "Bad[Name]"}]
        }
        """);
        var output = Path.Combine(_dir, "corrupt.xlsx");

        var act = () => _executor.ExecuteAsync(plan, output);

        await act.Should().ThrowAsync<InvalidOperationException>();
        File.Exists(output).Should().BeFalse();
        Directory.GetFiles(_dir).Should().BeEmpty("no temp remnants may survive a failed run");
    }
}
