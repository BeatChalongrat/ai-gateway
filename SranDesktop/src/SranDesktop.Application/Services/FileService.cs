using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

public sealed class FileService : IFileService
{
    private const long MaxFileSizeBytes = 50 * 1024 * 1024; // matches web-chat's practical limit

    private static readonly Dictionary<string, string> MimeByExtension = new(StringComparer.OrdinalIgnoreCase)
    {
        [".pdf"] = "application/pdf",
        [".txt"] = "text/plain",
        [".md"] = "text/markdown",
        [".csv"] = "text/csv",
        [".json"] = "application/json",
        [".xml"] = "application/xml",
        [".html"] = "text/html",
        [".docx"] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        [".xlsx"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        [".pptx"] = "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        [".png"] = "image/png",
        [".jpg"] = "image/jpeg",
        [".jpeg"] = "image/jpeg",
        [".gif"] = "image/gif",
        [".webp"] = "image/webp",
        [".zip"] = "application/zip"
    };

    private readonly IChatEdgeApiClient _api;
    private readonly ILogger<FileService> _logger;

    public FileService(IChatEdgeApiClient api, ILogger<FileService> logger)
    {
        _api = api;
        _logger = logger;
    }

    public async Task<FileAttachmentDto> UploadAsync(
        string conversationId, string localPath,
        IProgress<double>? progress = null, string? clientId = null, CancellationToken ct = default)
    {
        var info = new FileInfo(localPath);
        if (!info.Exists)
            throw new FileNotFoundException("File not found", localPath);
        if (info.Length > MaxFileSizeBytes)
            throw new InvalidOperationException(
                $"File is too large ({info.Length / (1024 * 1024)} MB - max {MaxFileSizeBytes / (1024 * 1024)} MB).");

        var contentType = MimeByExtension.TryGetValue(info.Extension, out var mime)
            ? mime
            : "application/octet-stream";

        await using var stream = new FileStream(
            localPath, FileMode.Open, FileAccess.Read, FileShare.Read, 81920, useAsync: true);

        var result = await _api.UploadFileAsync(
            conversationId, stream, info.Name, contentType, info.Length, progress, clientId, ct);

        _logger.LogInformation("Uploaded {Name} ({Bytes} bytes) as {Id}", info.Name, info.Length, result.Id);
        return result;
    }
}
