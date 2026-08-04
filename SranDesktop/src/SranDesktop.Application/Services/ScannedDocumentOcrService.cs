using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>
/// Server-side OCR for scanned PDFs (Option 1).
///
/// Windows' own OCR engine does not support Thai at all — it ships 25
/// languages and Thai is not one of them — and bundling Tesseract would put a
/// native binary next to a per-user installer. chat-edge already runs Tesseract
/// (tha+eng) inside the hardened sandbox for exactly this, and it is the same
/// pipeline the web client uses, so a document read on the desktop and the same
/// document read in the browser produce the same text.
///
/// Flow: upload → poll extraction_status → read the text. The upload is the
/// EXISTING <see cref="IFileService"/> path (dedup key and all), and extraction
/// runs asynchronously server-side, so the poll is not optional — the upload
/// response comes back long before the OCR does.
/// </summary>
public sealed class ScannedDocumentOcrService : IScannedDocumentOcr
{
    /// <summary>Matches the web client's extraction poll (1.5s). The work takes
    /// tens of seconds, so a tighter loop would only add requests.</summary>
    private static readonly TimeSpan DefaultPollInterval = TimeSpan.FromSeconds(1.5);

    /// <summary>Mirrors chat-edge's own extraction job timeout (8 minutes).
    /// Past that the server has given up, so waiting longer cannot help.</summary>
    private static readonly TimeSpan DefaultTimeout = TimeSpan.FromMinutes(8);

    private readonly IFileService _files;
    private readonly IChatEdgeApiClient _api;
    private readonly IConversationStore _store;
    private readonly ShellSession _session;
    private readonly ILogger<ScannedDocumentOcrService> _logger;
    private readonly TimeSpan _pollInterval;
    private readonly TimeSpan _timeout;

    public ScannedDocumentOcrService(
        IFileService files,
        IChatEdgeApiClient api,
        IConversationStore store,
        ShellSession session,
        ILogger<ScannedDocumentOcrService> logger,
        TimeSpan? pollInterval = null,
        TimeSpan? timeout = null)
    {
        _files = files;
        _api = api;
        _store = store;
        _session = session;
        _logger = logger;
        _pollInterval = pollInterval ?? DefaultPollInterval;
        _timeout = timeout ?? DefaultTimeout;
    }

    public async Task<ScannedOcrOutcome> ExtractAsync(string localPath, CancellationToken ct = default)
    {
        var conversationId = await ResolveServerConversationIdAsync(ct);
        if (conversationId is null)
        {
            return ScannedOcrOutcome.Failed(
                "Reading a scanned document needs a chat that is synced with the SRAN server, " +
                "and this one is not. Start the request in a normal chat and try again.");
        }

        string fileId;
        try
        {
            // Deterministic dedup key (FIX 10): reading the same unchanged file
            // twice reuses the server row instead of re-uploading and re-OCRing
            // it — the second read comes back in one poll.
            var uploaded = await _files.UploadAsync(
                conversationId, localPath, progress: null,
                clientId: DedupKey(localPath), ct: ct);
            fileId = uploaded.Id;
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Scanned-document upload failed for {Path}", localPath);
            return ScannedOcrOutcome.Failed(Offline(ex));
        }

        return await PollAsync(fileId, localPath, ct);
    }

    private async Task<ScannedOcrOutcome> PollAsync(string fileId, string localPath, CancellationToken ct)
    {
        var elapsed = Stopwatch.StartNew();
        while (true)
        {
            FileTextDto text;
            try
            {
                text = await _api.GetFileTextAsync(fileId, ct);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Reading extracted text failed for {FileId}", fileId);
                return ScannedOcrOutcome.Failed(Offline(ex));
            }

            switch (text.ExtractionStatus)
            {
                case "completed":
                    if (string.IsNullOrWhiteSpace(text.Text))
                    {
                        // OCR ran and found nothing readable. Saying so beats
                        // handing the model an empty string it would summarize
                        // as "the document is blank".
                        return ScannedOcrOutcome.Failed(
                            $"OCR ran on '{Path.GetFileName(localPath)}' but found no readable text. " +
                            "The scan may be too low-resolution, blank, or upside down.");
                    }
                    _logger.LogInformation(
                        "OCR completed for {Path} in {Seconds:F0}s ({Chars} chars)",
                        localPath, elapsed.Elapsed.TotalSeconds, text.Text!.Length);
                    return ScannedOcrOutcome.Ok(text.Text!, text.Truncated);

                case "failed":
                case "unsupported":
                    return ScannedOcrOutcome.Failed(
                        $"The server could not read '{Path.GetFileName(localPath)}' " +
                        $"(extraction {text.ExtractionStatus}).");
            }

            if (elapsed.Elapsed >= _timeout)
            {
                return ScannedOcrOutcome.Failed(
                    $"Timed out after {_timeout.TotalMinutes:F0} minutes waiting for OCR on " +
                    $"'{Path.GetFileName(localPath)}'. The document may be very long — " +
                    "try a smaller page range.");
            }

            await Task.Delay(_pollInterval, ct);
        }
    }

    /// <summary>
    /// The conversation to upload into: the one whose turn is running right now
    /// (the orchestrator publishes it on the ambient shell session before every
    /// tool call), mapped to its server row. Legacy local-first conversations
    /// carry the mirror id in ServerId; conversations created against the server
    /// already use the server id as their own — the same rule the transcript
    /// loader uses.
    /// </summary>
    private async Task<string?> ResolveServerConversationIdAsync(CancellationToken ct)
    {
        var localId = _session.CurrentConversationId;
        if (string.IsNullOrEmpty(localId))
            return null;

        try
        {
            var conversation = await _store.GetConversationAsync(localId, ct);
            return conversation?.ServerId ?? conversation?.Id ?? localId;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not resolve conversation {Id} for OCR upload", localId);
            return localId;
        }
    }

    /// <summary>Stable per (file, size, mtime) so an edited file re-uploads but
    /// an unchanged one does not.</summary>
    private static string DedupKey(string localPath)
    {
        var info = new FileInfo(localPath);
        var seed = $"{info.FullName}|{info.Length}|{info.LastWriteTimeUtc.Ticks}";
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(seed));
        return "ocr-" + Convert.ToHexString(hash)[..32].ToLowerInvariant();
    }

    /// <summary>One sentence the user can act on. Every failure to reach the
    /// server lands here: the distinguishing feature of this path is that it
    /// needs the network at all, and the user's next move is the same whether
    /// the cause was DNS, a dead service or an expired token.</summary>
    private static string Offline(Exception ex) =>
        "Reading a scanned document needs a connection to the SRAN server (the OCR runs there); " +
        $"the request failed: {ex.Message}. Documents that already contain text are still read " +
        "offline as usual.";
}
