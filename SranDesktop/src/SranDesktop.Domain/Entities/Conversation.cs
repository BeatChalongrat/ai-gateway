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

    /// <summary>
    /// Phase 14 — out of the main list but NOT deleted. Its own flag, never
    /// folded into a delete: an archived chat must still open and read, and
    /// the server keeps the two separate too (chat-edge migration 032).
    /// </summary>
    public bool IsArchived { get; set; }

    public string? ProjectId { get; set; }
    public ConversationSource Source { get; init; } = ConversationSource.Server;
    public DateTimeOffset? LastSyncedAt { get; set; }

    /// <summary>chat-edge conversation id this local conversation mirrors to
    /// (Phase 3 push sync). Null until the first successful sync.</summary>
    public string? ServerId { get; set; }
}
