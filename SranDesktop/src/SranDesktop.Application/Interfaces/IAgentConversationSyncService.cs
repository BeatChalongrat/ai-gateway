namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Push sync of agent-mode turns to chat-edge. Since FIX 9 the SERVER holds
/// the authoritative visible transcript (user + final assistant text) so web
/// and desktop show the same conversation; the local SQLite copy is the
/// offline cache plus the agent's private working state (tool chains,
/// screenshots), which never syncs. Disabled via
/// UserSettings.SyncAgentConversations.
/// </summary>
public interface IAgentConversationSyncService
{
    /// <summary>Pushes every local conversation's unsynced transcript.
    /// Silently skips when the setting is off or the server is unreachable
    /// (retried on the next trigger). Never throws for transport failures.</summary>
    Task SyncAllAsync(CancellationToken ct = default);

    /// <summary>Fire-and-forget, ~5s-debounced sync of one conversation —
    /// the retry path for turns that could not land synchronously.</summary>
    void QueueConversationSync(string conversationId);

    /// <summary>Awaited push of one conversation's unsynced transcript so a
    /// completed turn lands on the server before the UI treats it as done
    /// (FIX 9 Part A). Returns false when the push failed — the id is
    /// re-queued for the debounced retry and the caller may surface a
    /// "not synced yet" hint. Returns true when the transcript is on the
    /// server (or sync is disabled by the user's setting).</summary>
    Task<bool> SyncConversationNowAsync(string conversationId, CancellationToken ct = default);

    /// <summary>Raised when a push discovers the conversation was deleted on
    /// the server (another client) — the local copy has already been removed;
    /// the UI should drop the sidebar row (FIX 9 Part C). May fire on any
    /// thread.</summary>
    event EventHandler<string>? RemoteConversationDeleted;
}
