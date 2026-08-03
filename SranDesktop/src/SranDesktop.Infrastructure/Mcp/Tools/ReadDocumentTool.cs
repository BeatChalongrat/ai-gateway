using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Documents;

namespace SranDesktop.Infrastructure.Mcp.Tools;

/// <summary>
/// Reads the TEXT out of a document on this machine — PDF, Word, Excel,
/// PowerPoint, plain text. read_file returns base64 for anything binary, which
/// is useless to the model for a PDF or a .docx; this tool is the answer to
/// "summarize this file".
///
/// Same gates as every other read tool: PathGuard confines it to the allowed
/// roots, and SensitiveReadGuard makes a target outside the user's granted
/// folders prompt for consent first (see the read_document entry there).
/// DefaultPermission matches read_file — read-only, no side effects.
/// </summary>
public sealed class ReadDocumentTool : IMcpTool
{
    /// <summary>Bigger than read_file's 10 MB: a 40-page PDF with images is
    /// routinely 15-20 MB even though its text is a few hundred KB.</summary>
    private const long MaxFileBytes = 50 * 1024 * 1024;

    public string Name => "read_document";

    public string Description =>
        "Extract the readable text from a document file on this computer: PDF, Word (.docx), " +
        "Excel (.xlsx/.xlsm), PowerPoint (.pptx), and plain-text formats (.txt, .md, .csv, .json, …). " +
        "USE THIS — not read_file — for any document the user asks you to read, summarize, check or " +
        "answer questions about; read_file returns unusable base64 for these formats. " +
        "PDF output is labelled per page, Excel per sheet, PowerPoint per slide, so you can cite where " +
        "something came from. Long documents are truncated with an explicit note naming the totals. " +
        "Scanned PDFs (no text layer) cannot be read — there is no OCR.";

    public ToolCategory Category => ToolCategory.FileSystem;

    // Read-only, like read_file. Reads outside the user's granted folders are
    // still gated — SensitiveReadGuard raises those to a consent prompt.
    public PermissionLevel DefaultPermission => PermissionLevel.AllowAlways;

    public JsonElement ParametersSchema { get; } = ToolHelpers.Schema("""
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path of the document to read"
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Optional cap on extracted characters (default 40000). Lower it when you only need the gist of a large document."
                }
            },
            "required": ["path"]
        }
        """);

    public Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = PathGuard.Resolve(ToolHelpers.GetString(arguments, "path"), out var error);
        if (path is null)
            return Task.FromResult(ToolResult.Fail(error!));

        if (!File.Exists(path))
            return Task.FromResult(ToolResult.Fail($"File not found: {path}"));

        var info = new FileInfo(path);
        if (info.Length > MaxFileBytes)
        {
            return Task.FromResult(ToolResult.Fail(
                $"File too large: {ToolHelpers.FormatSize(info.Length)} (max 50 MB)."));
        }

        if (!DocumentTextExtractor.IsSupported(path))
        {
            return Task.FromResult(ToolResult.Fail(
                $"read_document does not handle '{Path.GetExtension(path)}' files. " +
                "It supports PDF, .docx, .xlsx/.xlsm, .pptx and plain-text formats. " +
                "For other file types use read_file."));
        }

        var maxChars = GetMaxChars(arguments);

        // Extraction is CPU-bound and synchronous in all three libraries; keep
        // it off the UI thread so a 200-page PDF cannot freeze the window.
        return Task.Run(() =>
        {
            ct.ThrowIfCancellationRequested();
            var result = DocumentTextExtractor.Extract(path, maxChars);
            if (!result.Success)
                return ToolResult.Fail(result.Error!);

            if (result.Text.Length == 0)
            {
                return ToolResult.Ok(
                    $"[{result.Kind} {info.Name} — {result.TotalUnits} {result.UnitLabel}(s), " +
                    "but no readable text was found]");
            }

            var header = $"[{result.Kind}: {info.Name} — {result.TotalUnits} {result.UnitLabel}(s), " +
                         $"{ToolHelpers.FormatSize(info.Length)}]";
            if (result.Truncated)
            {
                header += $"\n[TRUNCATED: showing {result.UnitsIncluded} of {result.TotalUnits} " +
                          $"{result.UnitLabel}(s). Ask for a specific range if you need the rest.]";
            }

            return ToolResult.Ok(header + "\n\n" + result.Text);
        }, ct);
    }

    private static int GetMaxChars(JsonElement args)
    {
        if (args.ValueKind == JsonValueKind.Object
            && args.TryGetProperty("max_chars", out var el)
            && el.ValueKind == JsonValueKind.Number
            && el.TryGetInt32(out var value))
        {
            // Floor keeps a mistaken max_chars:1 from producing a header with
            // no document in it; ceiling keeps one call from eating the context.
            return Math.Clamp(value, 500, DocumentTextExtractor.DefaultMaxChars);
        }
        return DocumentTextExtractor.DefaultMaxChars;
    }
}
