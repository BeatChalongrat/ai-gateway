using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>Local project persistence (R9 Feature B).</summary>
public interface IProjectStore
{
    Task<IReadOnlyList<Project>> GetAllAsync(CancellationToken ct = default);
    Task UpsertAsync(Project project, CancellationToken ct = default);
    Task DeleteAsync(string id, CancellationToken ct = default);

    /// <summary>Copy a file into the project's folder; returns the stored path.</summary>
    Task<string> AddFileAsync(Project project, string sourcePath, CancellationToken ct = default);

    Task RemoveFileAsync(Project project, string storedPath, CancellationToken ct = default);

    /// <summary>The project that owns a conversation, if any.</summary>
    Task<Project?> FindByConversationAsync(string conversationId, CancellationToken ct = default);
}
