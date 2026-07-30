using System.Text.Json;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;

namespace SranDesktop.DocumentWorker;

internal static class DocxOperations
{
    public static void Create(string path, JsonElement payload)
    {
        using var document = WordprocessingDocument.Create(
            path, WordprocessingDocumentType.Document);
        var main = document.AddMainDocumentPart();
        main.Document = new Document(new Body());
        var body = main.Document.Body!;

        foreach (var block in Blocks(payload))
            Append(body, block);

        if (!body.Elements<Paragraph>().Any())
            // Word treats a bodyless document as damaged; an empty request should
            // still produce a file that opens.
            body.AppendChild(new Paragraph(new Run(new Text(string.Empty))));
        main.Document.Save();
    }

    public static void Edit(string path, JsonElement payload)
    {
        using var document = WordprocessingDocument.Open(path, isEditable: true);
        var body = document.MainDocumentPart?.Document?.Body
                   ?? throw new InvalidOperationException(
                       "The document has no body part and cannot be edited.");

        if (payload.TryGetProperty("edits", out var edits)
            && edits.ValueKind == JsonValueKind.Array)
        {
            foreach (var edit in edits.EnumerateArray())
                ApplyEdit(body, edit);
        }
        document.MainDocumentPart!.Document.Save();
    }

    private static void ApplyEdit(Body body, JsonElement edit)
    {
        var operation = Str(edit, "op");
        switch (operation)
        {
            case "append_paragraph":
                Append(body, edit);
                break;

            case "replace_text":
            {
                var find = Str(edit, "find");
                var replace = Str(edit, "replace") ?? "";
                if (string.IsNullOrEmpty(find))
                    throw new InvalidOperationException("replace_text requires 'find'.");
                var matched = false;
                foreach (var text in body.Descendants<Text>())
                {
                    if (!text.Text.Contains(find, StringComparison.Ordinal))
                        continue;
                    text.Text = text.Text.Replace(find, replace, StringComparison.Ordinal);
                    matched = true;
                }
                if (!matched)
                    // Silently "succeeding" on a no-op would let the run report an
                    // edit that never happened.
                    throw new InvalidOperationException(
                        $"replace_text found no occurrence of '{find}'.");
                break;
            }

            case "replace_paragraph":
            {
                var index = Int(edit, "index") ?? throw new InvalidOperationException(
                    "replace_paragraph requires 'index'.");
                var paragraphs = body.Elements<Paragraph>().ToList();
                if (index < 0 || index >= paragraphs.Count)
                    throw new InvalidOperationException(
                        $"replace_paragraph index {index} is outside the document "
                        + $"({paragraphs.Count} paragraphs).");
                var replacement = BuildParagraph(Str(edit, "text") ?? "", Str(edit, "style"));
                body.ReplaceChild(replacement, paragraphs[index]);
                break;
            }

            case "delete_paragraph":
            {
                var index = Int(edit, "index") ?? throw new InvalidOperationException(
                    "delete_paragraph requires 'index'.");
                var paragraphs = body.Elements<Paragraph>().ToList();
                if (index < 0 || index >= paragraphs.Count)
                    throw new InvalidOperationException(
                        $"delete_paragraph index {index} is outside the document.");
                paragraphs[index].Remove();
                break;
            }

            case "set_table_cell":
            {
                var tableIndex = Int(edit, "table") ?? 0;
                var row = Int(edit, "row") ?? throw new InvalidOperationException(
                    "set_table_cell requires 'row'.");
                var column = Int(edit, "column") ?? throw new InvalidOperationException(
                    "set_table_cell requires 'column'.");
                var tables = body.Elements<Table>().ToList();
                if (tableIndex < 0 || tableIndex >= tables.Count)
                    throw new InvalidOperationException(
                        $"set_table_cell table {tableIndex} does not exist.");
                var rows = tables[tableIndex].Elements<TableRow>().ToList();
                if (row < 0 || row >= rows.Count)
                    throw new InvalidOperationException($"set_table_cell row {row} does not exist.");
                var cells = rows[row].Elements<TableCell>().ToList();
                if (column < 0 || column >= cells.Count)
                    throw new InvalidOperationException(
                        $"set_table_cell column {column} does not exist.");
                cells[column].RemoveAllChildren<Paragraph>();
                cells[column].AppendChild(BuildParagraph(Str(edit, "text") ?? "", null));
                break;
            }

            case "append_table":
                Append(body, edit);
                break;

            default:
                throw new InvalidOperationException(
                    $"'{operation}' is not a supported DOCX edit.");
        }
    }

    private static IEnumerable<JsonElement> Blocks(JsonElement payload) =>
        payload.TryGetProperty("blocks", out var blocks)
        && blocks.ValueKind == JsonValueKind.Array
            ? blocks.EnumerateArray()
            : [];

    private static void Append(Body body, JsonElement block)
    {
        switch (Str(block, "type") ?? Str(block, "op"))
        {
            case "heading":
                body.AppendChild(BuildParagraph(
                    Str(block, "text") ?? "",
                    $"Heading{Math.Clamp(Int(block, "level") ?? 1, 1, 6)}"));
                break;

            case "page_break":
                body.AppendChild(new Paragraph(
                    new Run(new Break { Type = BreakValues.Page })));
                break;

            case "table":
            case "append_table":
            {
                var rows = block.TryGetProperty("rows", out var rowsElement)
                           && rowsElement.ValueKind == JsonValueKind.Array
                    ? rowsElement
                    : throw new InvalidOperationException("A table block requires 'rows'.");
                var table = new Table(new TableProperties(
                    new TableBorders(
                        new TopBorder { Val = BorderValues.Single, Size = 4 },
                        new BottomBorder { Val = BorderValues.Single, Size = 4 },
                        new LeftBorder { Val = BorderValues.Single, Size = 4 },
                        new RightBorder { Val = BorderValues.Single, Size = 4 },
                        new InsideHorizontalBorder { Val = BorderValues.Single, Size = 4 },
                        new InsideVerticalBorder { Val = BorderValues.Single, Size = 4 })));
                foreach (var row in rows.EnumerateArray())
                {
                    var tableRow = new TableRow();
                    foreach (var cell in row.EnumerateArray())
                        tableRow.AppendChild(new TableCell(
                            new TableCellProperties(
                                new TableCellWidth { Type = TableWidthUnitValues.Auto }),
                            BuildParagraph(cell.ToString(), null)));
                    table.AppendChild(tableRow);
                }
                body.AppendChild(table);
                break;
            }

            default:
                body.AppendChild(BuildParagraph(
                    Str(block, "text") ?? "", Str(block, "style")));
                break;
        }
    }

    private static Paragraph BuildParagraph(string text, string? style)
    {
        var paragraph = new Paragraph();
        if (!string.IsNullOrEmpty(style))
            paragraph.AppendChild(new ParagraphProperties(
                new ParagraphStyleId { Val = style }));
        paragraph.AppendChild(new Run(new Text(text) { Space = SpaceProcessingModeValues.Preserve }));
        return paragraph;
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
}
