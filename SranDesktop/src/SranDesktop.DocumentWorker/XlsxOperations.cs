using System.Text.Json;
using ClosedXML.Excel;

namespace SranDesktop.DocumentWorker;

internal static class XlsxOperations
{
    public static void Create(string path, JsonElement payload)
    {
        using var workbook = new XLWorkbook();
        var sheets = payload.TryGetProperty("sheets", out var element)
                     && element.ValueKind == JsonValueKind.Array
            ? element.EnumerateArray().ToList()
            : [];

        if (sheets.Count == 0)
            workbook.Worksheets.Add("Sheet1");
        foreach (var sheet in sheets)
            BuildSheet(workbook, sheet);

        workbook.SaveAs(path);
    }

    public static void Edit(string path, JsonElement payload)
    {
        using var workbook = new XLWorkbook(path);
        if (payload.TryGetProperty("edits", out var edits)
            && edits.ValueKind == JsonValueKind.Array)
        {
            foreach (var edit in edits.EnumerateArray())
                ApplyEdit(workbook, edit);
        }
        workbook.Save();
    }

    private static void BuildSheet(XLWorkbook workbook, JsonElement sheet)
    {
        var name = Str(sheet, "name") ?? $"Sheet{workbook.Worksheets.Count + 1}";
        var worksheet = workbook.Worksheets.Add(name);

        if (sheet.TryGetProperty("rows", out var rows)
            && rows.ValueKind == JsonValueKind.Array)
        {
            var rowIndex = 1;
            foreach (var row in rows.EnumerateArray())
            {
                var columnIndex = 1;
                foreach (var value in row.EnumerateArray())
                    SetCell(worksheet.Cell(rowIndex, columnIndex++), value);
                rowIndex++;
            }
        }

        if (sheet.TryGetProperty("cells", out var cells)
            && cells.ValueKind == JsonValueKind.Array)
        {
            foreach (var cell in cells.EnumerateArray())
                SetNamedCell(worksheet, cell);
        }

        if (Int(sheet, "freeze_rows") is { } freezeRows and > 0)
            worksheet.SheetView.FreezeRows(freezeRows);
        if (Bool(sheet, "auto_filter") == true && worksheet.RangeUsed() is { } used)
            used.SetAutoFilter();
    }

    private static void ApplyEdit(XLWorkbook workbook, JsonElement edit)
    {
        var operation = Str(edit, "op");
        switch (operation)
        {
            case "set_cell":
            {
                var worksheet = Sheet(workbook, edit);
                SetNamedCell(worksheet, edit);
                break;
            }

            case "add_sheet":
            {
                var name = Str(edit, "name")
                           ?? throw new InvalidOperationException("add_sheet requires 'name'.");
                if (workbook.Worksheets.Any(sheet =>
                        string.Equals(sheet.Name, name, StringComparison.OrdinalIgnoreCase)))
                    throw new InvalidOperationException($"Sheet '{name}' already exists.");
                BuildSheet(workbook, edit);
                break;
            }

            case "delete_sheet":
            {
                var worksheet = Sheet(workbook, edit);
                if (workbook.Worksheets.Count == 1)
                    // A workbook with no sheets does not open.
                    throw new InvalidOperationException(
                        "Deleting the only worksheet would produce an unopenable workbook.");
                worksheet.Delete();
                break;
            }

            case "set_formula":
            {
                var worksheet = Sheet(workbook, edit);
                var reference = Str(edit, "cell")
                                ?? throw new InvalidOperationException(
                                    "set_formula requires 'cell'.");
                var formula = Str(edit, "formula")
                              ?? throw new InvalidOperationException(
                                  "set_formula requires 'formula'.");
                worksheet.Cell(reference).FormulaA1 =
                    formula.StartsWith('=') ? formula[1..] : formula;
                break;
            }

            case "set_number_format":
            {
                var worksheet = Sheet(workbook, edit);
                var range = Str(edit, "range")
                            ?? throw new InvalidOperationException(
                                "set_number_format requires 'range'.");
                worksheet.Range(range).Style.NumberFormat.Format =
                    Str(edit, "format")
                    ?? throw new InvalidOperationException(
                        "set_number_format requires 'format'.");
                break;
            }

            default:
                throw new InvalidOperationException(
                    $"'{operation}' is not a supported XLSX edit.");
        }
    }

    private static IXLWorksheet Sheet(XLWorkbook workbook, JsonElement element)
    {
        var name = Str(element, "sheet");
        if (string.IsNullOrEmpty(name))
            return workbook.Worksheets.First();
        return workbook.Worksheets.FirstOrDefault(sheet =>
                   string.Equals(sheet.Name, name, StringComparison.OrdinalIgnoreCase))
               ?? throw new InvalidOperationException($"Sheet '{name}' does not exist.");
    }

    private static void SetNamedCell(IXLWorksheet worksheet, JsonElement element)
    {
        var reference = Str(element, "cell")
                        ?? throw new InvalidOperationException(
                            "A cell operation requires 'cell' (e.g. \"B2\").");
        if (element.TryGetProperty("formula", out var formula)
            && formula.ValueKind == JsonValueKind.String)
        {
            var text = formula.GetString()!;
            worksheet.Cell(reference).FormulaA1 = text.StartsWith('=') ? text[1..] : text;
            return;
        }
        if (element.TryGetProperty("value", out var value))
            SetCell(worksheet.Cell(reference), value);
    }

    private static void SetCell(IXLCell cell, JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Number when value.TryGetDouble(out var number):
                cell.Value = number;
                break;
            case JsonValueKind.True:
            case JsonValueKind.False:
                cell.Value = value.GetBoolean();
                break;
            case JsonValueKind.Null:
                cell.Clear(XLClearOptions.Contents);
                break;
            default:
            {
                var text = value.ValueKind == JsonValueKind.String
                    ? value.GetString() ?? ""
                    : value.ToString();
                // A leading '=' from untrusted data would silently become a live
                // formula; only an explicit "formula" field may do that.
                if (text.StartsWith('=') || text.StartsWith('+') || text.StartsWith('@'))
                    cell.SetValue(text).Style.NumberFormat.Format = "@";
                else
                    cell.Value = text;
                break;
            }
        }
    }

    private static string? Str(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static int? Int(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.TryGetInt32(out var number)
            ? number
            : null;

    private static bool? Bool(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.ValueKind is JsonValueKind.True or JsonValueKind.False
            ? value.GetBoolean()
            : null;
}
