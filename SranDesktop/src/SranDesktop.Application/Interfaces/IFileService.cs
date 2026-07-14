using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

public interface IFileService
{
    /// <summary>Uploads a local file to a conversation. Throws on oversize or unreadable files.</summary>
    Task<FileAttachmentDto> UploadAsync(
        string conversationId, string localPath,
        IProgress<double>? progress = null, CancellationToken ct = default);
}
