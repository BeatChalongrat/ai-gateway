namespace SranDesktop.Domain.Entities;

/// <summary>
/// A short, durable, model-authored note scoped to one conversation (Phase B
/// PART 3). Survives restarts so the agent recalls key facts ("this project
/// builds with dotnet build src/X.sln", discovered paths, user preferences)
/// without re-discovering them. Plain text, conversation-scoped — never global,
/// so it can't leak between conversations.
/// </summary>
public sealed class AgentNote
{
    public required string Id { get; init; }
    public required string ConversationId { get; init; }
    public required string Content { get; init; }
    public required DateTimeOffset CreatedAt { get; init; }
}
