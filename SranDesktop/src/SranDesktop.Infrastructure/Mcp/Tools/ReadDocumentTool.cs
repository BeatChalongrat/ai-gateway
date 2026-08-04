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
///
/// A PDF with no text layer (a scan) is the one file no offline library can
/// read, so with an <see cref="IScannedDocumentOcr"/> wired it goes to
/// chat-edge, whose OCR reads Thai and English, and the text comes back.
///
/// That is NOT a separate permission decision, and asking per file would be
/// theatre: the desktop model runs on the gateway, so every document this tool
/// reads is already sent to the server as prompt text — a scan differs only in
/// sending image bytes instead of characters. The real gates are the ones that
/// decide WHICH files the agent may touch at all (PathGuard + granted folders +
/// SensitiveReadGuard), and they run before any of this. What the user gets
/// instead is a one-time notice the first time a scan is uploaded.
/// </summary>
public sealed class ReadDocumentTool : IMcpTool
{
    /// <summary>Bigger than read_file's 10 MB: a 40-page PDF with images is
    /// routinely 15-20 MB even though its text is a few hundred KB.</summary>
    private const long MaxFileBytes = 50 * 1024 * 1024;

    /// <summary>Long enough to actually read — this appears once, ever.</summary>
    private static readonly TimeSpan NoticeDuration = TimeSpan.FromSeconds(8);

    private readonly IScannedDocumentOcr? _ocr;
    private readonly ISettingsService? _settings;
    private readonly IToastService? _toasts;

    /// <param name="ocr">Server-side OCR for scanned PDFs. Null (the default,
    /// used by tests and any headless host) keeps the tool strictly offline:
    /// a scan then fails with the same explanation it always gave.</param>
    /// <param name="settings">Holds the "notice already shown" flag. It is a
    /// UI seen-marker, not a permission — nothing reads it to decide whether
    /// the upload may happen.</param>
    /// <param name="toasts">Where the one-time notice goes. Both this and
    /// <paramref name="settings"/> are optional; without them the OCR path
    /// works exactly the same, just silently.</param>
    public ReadDocumentTool(
        IScannedDocumentOcr? ocr = null,
        ISettingsService? settings = null,
        IToastService? toasts = null)
    {
        _ocr = ocr;
        _settings = settings;
        _toasts = toasts;
    }

    public string Name => "read_document";

    public string Description =>
        "Extract the readable text from a document file on this computer: PDF, Word (.docx), " +
        "Excel (.xlsx/.xlsm), PowerPoint (.pptx), and plain-text formats (.txt, .md, .csv, .json, …). " +
        "USE THIS — not read_file — for any document the user asks you to read, summarize, check or " +
        "answer questions about; read_file returns unusable base64 for these formats. " +
        "PDF output is labelled per page, Excel per sheet, PowerPoint per slide, so you can cite where " +
        "something came from. Long documents are truncated with an explicit note naming the totals. " +
        (_ocr is null
            ? "Scanned PDFs (no text layer) cannot be read — there is no OCR."
            : "Scanned PDFs (no text layer) are read by OCR on the SRAN server, which handles Thai and " +
              "English — just call this tool for them too.");

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

    public async Task<ToolResult> ExecuteAsync(JsonElement arguments, CancellationToken ct)
    {
        var path = PathGuard.Resolve(ToolHelpers.GetString(arguments, "path"), out var error);
        if (path is null)
            return ToolResult.Fail(error!);

        if (!File.Exists(path))
            return ToolResult.Fail($"File not found: {path}");

        var info = new FileInfo(path);
        if (info.Length > MaxFileBytes)
            return ToolResult.Fail($"File too large: {ToolHelpers.FormatSize(info.Length)} (max 50 MB).");

        if (!DocumentTextExtractor.IsSupported(path))
        {
            return ToolResult.Fail(
                $"read_document does not handle '{Path.GetExtension(path)}' files. " +
                "It supports PDF, .docx, .xlsx/.xlsm, .pptx and plain-text formats. " +
                "For other file types use read_file.");
        }

        var maxChars = GetMaxChars(arguments);

        // Extraction is CPU-bound and synchronous in all three libraries; keep
        // it off the UI thread so a 200-page PDF cannot freeze the window.
        var result = await Task.Run(() =>
        {
            ct.ThrowIfCancellationRequested();
            return DocumentTextExtractor.Extract(path, maxChars);
        }, ct);

        // No text layer: the only way to read it is OCR, which lives on the
        // server. Everything about that path — the prompt, the upload, the
        // wait — is behind this branch, so a normal document never touches it.
        if (result.NeedsOcr)
            return await ReadScannedAsync(path, info, result, maxChars, ct);

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
    }

    /// <summary>
    /// The scanned-PDF path: upload, wait, return the OCR text. The file has
    /// already cleared PathGuard and (for anything outside the user's granted
    /// folders) SensitiveReadGuard by the time this runs.
    /// </summary>
    private async Task<ToolResult> ReadScannedAsync(
        string path, FileInfo info, DocumentExtraction scan, int maxChars, CancellationToken ct)
    {
        if (_ocr is null)
            return ToolResult.Fail(scan.Error!); // strictly-offline host: unchanged behaviour

        ShowUploadNoticeOnce();

        var outcome = await _ocr.ExtractAsync(path, ct);
        if (!outcome.Success)
            return ToolResult.Fail(outcome.Error!);

        var text = Clamp(outcome.Text, maxChars);
        var header = $"[Scanned PDF: {info.Name} — {scan.TotalUnits} page(s), " +
                     $"{ToolHelpers.FormatSize(info.Length)} — text recovered by OCR on the SRAN server]";
        if (outcome.Truncated || text.Length < outcome.Text.Length)
        {
            header += "\n[TRUNCATED: this is the beginning of the document, not all of it. " +
                      "Say so if you summarize it.]";
        }
        // OCR output is imperfect by nature; the model must not present a
        // mis-read name or number as if it had been typed.
        header += "\n[OCR text may contain recognition errors — quote it with care.]";

        return ToolResult.Ok(header + "\n\n" + text);
    }

    /// <summary>
    /// Tells the user, once in the life of the install, that scanned pages are
    /// read on the server. Informational: it does not gate the upload and it
    /// never appears again, so the second scan of the day is silent.
    ///
    /// Best-effort by construction — a notice must never be the reason a
    /// document fails to read, so a settings file that cannot be written costs
    /// at most a repeated toast.
    /// </summary>
    private void ShowUploadNoticeOnce()
    {
        if (_settings is null || _toasts is null || _settings.Current.ScannedUploadNoticeShown)
            return;

        _toasts.Show(ToastKind.Info, "toast.scannedUploadNotice", duration: NoticeDuration);

        try
        {
            var updated = _settings.Current.Clone();
            updated.ScannedUploadNoticeShown = true;
            _settings.Save(updated);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
        }
    }

    /// <summary>Caps the returned text without splitting a surrogate pair.</summary>
    private static string Clamp(string text, int maxChars)
    {
        if (text.Length <= maxChars)
            return text;
        var end = maxChars;
        if (char.IsHighSurrogate(text[end - 1]))
            end--;
        return text[..end];
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
