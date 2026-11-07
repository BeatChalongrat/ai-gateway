namespace SranDesktop.Domain.Entities;

/// <summary>
/// A short, durable, model-authored note (Phase B PART 3). Survives restarts
/// so the agent recalls key facts ("this project builds with dotnet build
/// src/X.sln", discovered paths, user preferences) without re-discovering
/// them. Usually scoped to one conversation; durable user-level facts may be
/// stored GLOBALLY under the reserved IConversationStore.GlobalNotesId so they
/// carry across conversations (Global Memory).
/// </summary>
public sealed class AgentNote
{
    public required string Id { get; init; }
    public required string ConversationId { get; init; }
    public required string Content { get; init; }
    public required DateTimeOffset CreatedAt { get; init; }
}
