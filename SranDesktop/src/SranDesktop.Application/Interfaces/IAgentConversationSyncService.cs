namespace SranDesktop.Application.Interfaces;

/// <summary>
/// One-way push backup of local agent-mode conversations to chat-edge
/// (Phase 3 D). The local SQLite copy stays authoritative; the server holds a
/// mirror deduped by client ids. Disabled via UserSettings.SyncAgentConversations.
/// </summary>
public interface IAgentConversationSyncService
{
    /// <summary>Pushes every local agent conversation's unsynced transcript.
    /// Silently skips when the setting is off or the server is unreachable
    /// (retried on the next trigger). Never throws for transport failures.</summary>
    Task SyncAllAsync(CancellationToken ct = default);

    /// <summary>Fire-and-forget, ~5s-debounced sync of one conversation —
    /// called after a turn persists so the UI path never blocks on sync.</summary>
    void QueueConversationSync(string conversationId);
}
