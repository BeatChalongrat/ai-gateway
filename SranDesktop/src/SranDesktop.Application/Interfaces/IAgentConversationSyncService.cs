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

    /// <summary>Fire-and-forget, ~5s-debounced sync of one conversa