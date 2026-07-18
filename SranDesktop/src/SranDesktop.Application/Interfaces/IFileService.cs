using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

public interface IFileService
{
    /// <summary>Uploads a local file to a conversation. Throws on oversize or
    /// unreadable files. <paramref name="clientId"/> (FIX 10) is the caller's
    /// deterministic dedup key — a re-upload of the same key returns the
    /// existing server row instead of dup