using System.Text.Json;
using ClosedXML.Excel;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Office;

/// <summary>
/// On-device :::excel-plan executor (Phase 12 E), interpreting the same plan
/// JSON as the server's scripts/excel_executor.py but through ClosedXML (MIT).
///
/// Supported locally: create_workbook plans with create_sheet / delete_sheet /
/// rename_sheet / write_cells / write_table / format_range / set_column_width /
/// merge_cells / auto_filter / freeze_panes.
/// NOT supported (CanExecute false → server fallback): modify_workbook or any
/// source_file_id (the uploaded source lives server-side), create_chart
/// (ClosedXML cannot create charts) and unknown action types.
///
/// Failure semantics mirror the server: a bad ACTION is logged as
/// "ERROR in &lt;type&gt;: …" and the plan continues; only a failure to produce a
/// workbook at all throws (and leaves no partial output file behind).
/// </summary>
public sealed class LocalExcelPlanExecutor : ILocalPlanExecutor
{
    private static readonly HashSet<string> SupportedActions = new(StringComparer.Ordinal)
    {
        "create_sheet", "delete_sheet", "rename_sheet", "write_cells", "write_table",
        "format_range", "set_column_width", "merge_cells", "auto_filter", "freeze_panes",
    };

    public string PlanType => "excel";

    public bool CanExecute(JsonElement plan)
    {
        if (plan.ValueKind != JsonValueKind.Object)
            return false;
        // Editing an uploaded workbook needs the source bytes, which only the
        // server has — modify_workbook (the server default when the field is
        // absent) always falls back.
        if (!string.IsNullOrEmpty(PlanJson.GetString(plan, "source_file_id")))
            return false;
        if (PlanJson.GetString(plan, "operation") != "create_workbook")
            return false;
        if (!PlanJson.TryGetArray(plan, "actions", out var actions))
            return false;

        foreach (var action in actions.EnumerateArray())
        {
            if (action.ValueKind != JsonValueKind.Object)
                return false;
            var type = PlanJson.GetString(action, "type");
            if (type is null || !SupportedActions.Contains(type))
                return false; // create_chart / unknown op → server
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

        // Build into a temp sibling and move on success only — a failure
        // mid-generation must never leave a corrupt file at outputPath.
        // ClosedXML validates the save extension, so the temp name keeps it.
        var tempPath = Path.Combine(
            Path.GetDirectoryName(fullPath)!,
            $"{Path.GetFileNameWithoutExtension(fullPath)}.{Guid.NewGuid():N}.tmp{Path.GetExtension(fullPath)}");
        try
        {
            var changes = new List<string>();
            var sheetCount = 0;
            using (var wb = new XLWorkbook())
            {
                if (PlanJson.TryGetArray(plan, "actions", out var actions))
                {
                    foreach (var action in actions.EnumerateArray())
                    {
                        ct.ThrowIfCancellationRequested();
                        var type = PlanJson.GetString(action, "type") ?? "<missing type>";
                        try
                        {
                            changes.Add(Apply(wb, type, action));
                        }
                        catch (Exception ex) when (ex is not OperationCanceledException)
                        {
                            // Per-action isolation, same as the server executor.
                            changes.Add($"ERROR in {type}: {ex.Message}");
                        }
                    }
                }

                sheetCount = wb.Worksheets.Count;
                if (sheetCount == 0)
                    throw new InvalidOperationException(
                        "plan produced a workbook with no worksheets");

                wb.SaveAs(tempPath);
            }

            File.Move(tempPath, fullPath, overwrite: true);
            return new LocalPlanExecutionResult(
                fullPath, new FileInfo(fullPath).Length, changes,
                new Dictionary<string, int>
                {
                    ["sheets"] = sheetCount,
                    ["charts"] = 0, // charts are never generated locally
                });
        }
        catch
        {
            TryDelete(tempPath);
            throw;
        }
    }

    private static string Apply(XLWorkbook wb, string type, JsonElement action) => type switch
    {
        "create_sheet" => CreateSheet(wb, action),
        "delete_sheet" => DeleteSheet(wb, action),
        "rename_sheet" => RenameSheet(wb, action),
        "write_cells" => WriteCells(wb, action),
        "write_table" => WriteTable(wb, action),
        "format_range" => FormatRange(wb, action),
        "set_column_width" => SetColumnWidth(wb, action),
        "merge_cells" => MergeCells(wb, action),
        "auto_filter" => AutoFilter(wb, action),
        "freeze_panes" => FreezePanes(wb, action),
        _ => throw new InvalidOperationException("unknown action"), // unreachable: CanExecute gates
    };

    private static IXLWorksheet SheetFor(XLWorkbook wb, JsonElement action)
    {
        var name = PlanJson.GetString(action, "sheet_name")
                   ?? throw new InvalidOperationException("sheet_name missing");
        if (!wb.Worksheets.TryGetWorksheet(name, out var ws))
            throw new KeyNotFoundException($"sheet '{name}' not found");
        return ws;
    }

    private static string CreateSheet(XLWorkbook wb, JsonElement action)
    {
        var name = PlanJson.GetString(action, "sheet_name")
                   ?? throw new InvalidOperationException("sheet_name missing");
        var pos = PlanJson.GetInt(action, "position") ?? wb.Worksheets.Count;
        var ws = wb.AddWorksheet(name);
        // Plan positions are 0-based (openpyxl); ClosedXML's are 1-based.
        ws.Position = Math.Clamp(pos + 1, 1, wb.Worksheets.Count);
        return $"Created sheet '{name}' at position {pos}";
    }

    private static string DeleteSheet(XLWorkbook wb, JsonElement action)
    {
        var name = PlanJson.GetString(action, "sheet_name") ?? "";
        if (wb.Worksheets.TryGetWorksheet(name, out var ws))
        {
            ws.Delete();
            return $"Deleted sheet '{name}'";
        }
        return $"Skipped delete_sheet — '{name}' did not exist";
    }

    private static string RenameSheet(XLWorkbook wb, JsonElement action)
    {
        var ws = SheetFor(wb, action);
        var oldName = ws.Name;
        ws.Name = PlanJson.GetString(action, "new_name")
                  ?? throw new InvalidOperationException("new_name missing");
        return $"Renamed sheet '{oldName}' to '{ws.Name}'";
    }

    private static string WriteCells(XLWorkbook wb, JsonElement action)
    {
        var ws = SheetFor(wb, action);
        var count = 0;
        if (PlanJson.TryGetArray(action, "cells", out var cells))
        {
            foreach (var spec in cells.EnumerateArray())
            {
                if (spec.ValueKind != JsonValueKind.Object)
                    continue;
                var cell = ws.Cell(PlanJson.GetString(spec, "cell")
                                   ?? throw new InvalidOperationException("cell missing"));
                if (PlanJson.GetString(spec, "value_formula") is { } formula)
                    cell.FormulaA1 = formula;
                else if (spec.TryGetProperty("value", out var value))
                    SetCellValue(cell, value);

                if (PlanJson.GetBool(spec, "bold"))
                    cell.Style.Font.Bold = true;
                if (PlanJson.GetDouble(spec, "font_size") is { } size)
                    cell.Style.Font.FontSize = size;
                if (PlanJson.GetString(spec, "number_format") is { } format)
                    cell.Style.NumberFormat.Format = format;
                count++;
            }
        }
        return $"Wrote {count} cells in '{ws.Name}'";
    }

    private static string WriteTable(XLWorkbook wb, JsonElement action)
    {
        var ws = SheetFor(wb, action);
        var anchor = ws.Cell(PlanJson.GetString(action, "start_cell") ?? "A1");
        var row = anchor.Address.RowNumber;
        var col = anchor.Address.ColumnNumber;

        if (PlanJson.TryGetArray(action, "headers", out var headers))
        {
            var i = 0;
            foreach (var header in headers.EnumerateArray())
            {
                var cell = ws.Cell(row, col + i);
                SetCellValue(cell, header);
                cell.Style.Font.Bold = true;
                i++;
            }
        }

        var rows = 0;
        if (PlanJson.TryGetArray(action, "data", out var data))
        {
            foreach (var rowData in data.EnumerateArray())
            {
                if (rowData.ValueKind == JsonValueKind.Array)
                {
                    var c = 0;
                    foreach (var value in rowData.EnumerateArray())
                    {
                        SetCellValue(ws.Cell(row + 1 + rows, col + c), value);
                        c++;
                    }
                }
                rows++;
            }
        }

        var start = anchor.Address.ToString();
        return rows > 0
            ? $"Wrote table ({rows} rows) at {start} in '{ws.Name}'"
            : $"Wrote table headers at {start} in '{ws.Name}'";
    }

    private static string FormatRange(XLWorkbook wb, JsonElement action)
    {
        var ws = SheetFor(wb, action);
        var rangeRef = PlanJson.GetString(action, "range")
                       ?? throw new InvalidOperationException("range missing");
        var range = ws.Range(rangeRef);

        if (PlanJson.GetString(action, "fill_color") is { } fill)
            range.Style.Fill.BackgroundColor = ParseColor(fill);
        if (PlanJson.GetBool(action, "bold"))
            range.Style.Font.Bold = true;
        if (PlanJson.GetString(action, "font_color") is { } fontColor)
            range.Style.Font.FontColor = ParseColor(fontColor);
        if (PlanJson.GetDouble(action, "font_size") is { } size)
            range.Style.Font.FontSize = size;
        return $"Formatted range {rangeRef} in '{ws.Name}'";
    }

    private static string SetColumnWidth(XLWorkbook wb, JsonElement action)
    {
        var ws = SheetFor(wb, action);
        var count = 0;
        if (PlanJson.TryGetObject(action, "columns", out var columns))
        {
            foreach (var entry in columns.EnumerateObject())
            {
                if (entry.Value.ValueKind == JsonValueKind.Number)
                {
                    ws.Column(entry.Name).Width = entry.Value.GetDouble();
                    count++;
                }
            }
        }
        return $"Set {count} column widths in '{ws.Name}'";
    }

    private static string MergeCells(XLWorkbook wb, JsonElement action)
    {
        var ws = SheetFor(wb, action);
        var rangeRef = PlanJson.GetString(action, "range")
                       ?? throw new InvalidOperationException("range missing");
        ws.Range(rangeRef).Merge();
        return $"Merged cells {rangeRef} in '{ws.Name}'";
    }

    private static string AutoFilter(XLWorkbook wb, JsonElement action)
    {
        var ws = SheetFor(wb, action);
        var rangeRef = PlanJson.GetString(action, "range")
                       ?? throw new InvalidOperationException("range missing");
        ws.Range(rangeRef).SetAutoFilter();
        return $"Added filter to {rangeRef} in '{ws.Name}'";
    }

    private static string FreezePanes(XLWorkbook wb, JsonElement action)
    {
        var ws = SheetFor(wb, action);
        var cellRef = PlanJson.GetString(action, "cell")
                      ?? throw new InvalidOperationException("cell missing");
        var cell = ws.Cell(cellRef);
        ws.SheetView.Freeze(cell.Address.RowNumber - 1, cell.Address.ColumnNumber - 1);
        return $"Froze panes at {cellRef} in '{ws.Name}'";
    }

    private static void SetCellValue(IXLCell cell, JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Number:
                cell.Value = value.GetDouble();
                break;
            case JsonValueKind.True:
            case JsonValueKind.False:
                cell.Value = value.GetBoolean();
                break;
            case JsonValueKind.String:
                cell.Value = value.GetString();
                break;
            case JsonValueKind.Null:
            case JsonValueKind.Undefined:
                break;
            default:
                cell.Value = value.GetRawText();
                break;
        }
    }

    /// <summary>Plans carry openpyxl-style hex ("FF0000" or ARGB "FFFF0000",
    /// occasionally "#RRGGBB"); normalize to the #RRGGBB ClosedXML wants.</summary>
    private static XLColor ParseColor(string hex)
    {
        var h = hex.TrimStart('#');
        if (h.Length == 8)
            h = h[2..]; // drop the alpha byte
        return XLColor.FromHtml("#" + h);
    }

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
