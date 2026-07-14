using SranDesktop.Domain.Enums;

namespace SranDesktop.Domain.Entities;

public sealed class Conversation
{
    public required string Id { get; init; }
    public string? Title { get; set; }
    public string? ModelId { get; set; }
    public required DateTimeOffset CreatedAt { get; init; }
    public DateTimeOffset UpdatedAt { get; set; }
    public bool IsPinned { get; set; }
    public string? FolderId { get; set; }
    public ConversationSource Source { get; init; } = ConversationSource.Server;
    public DateTimeOffset? LastSyncedAt { get; set; }

    /// <summary>chat-edge conversation id this local conversation mirrors to
    /// (Phase 3 push sync). Null until the first successful sync.</summary>
    public string? ServerId { get; set; }
}
