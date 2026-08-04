namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Reads a document that has no text layer (a scan) by sending it to
/// chat-edge, whose OCR pipeline handles Thai + English. Everything local
/// stays local — this is only ever reached for files the offline extractor
/// has already proven it cannot read.
///
/// The caller is responsible for asking the user first: this interface
/// performs an upload, which moves the document off the machine.
/// </summary>
public interface IScannedDocumentOcr
{
    Task<ScannedOcrOutcome> ExtractAsync(string localPath, CancellationToken ct = default);
}

/// <summary>
/// Result of one server-side OCR read. Exactly one of <see cref="Text"/> /
/// <see cref="Error"/> is meaningful, per <see cref="Success"/>.
/// </summary>
/// <param name="Truncated">The server capped the extraction; the text is the
/// start of the document, not all of it.</param>
public sealed record ScannedOcrOutcome(bool Success, string Text, bool Truncated, string? Error)
{
    public static ScannedOcrOutcome Ok(string text, bool truncated) =>
        new(true, text, truncated, null);

    public static ScannedOcrOutcome Failed(string error) =>
        new(false, string.Empty, false, error);
}
