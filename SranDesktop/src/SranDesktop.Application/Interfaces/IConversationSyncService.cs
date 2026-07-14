namespace SranDesktop.Application.Interfaces;

public interface IConversationSyncService
{
    /// <summary>
    /// Pulls the server conversation list into the local SQLite store.
    /// Returns true if the sync completed (server reachable), false otherwise.
    /// </summary>
    Task<bool> SyncFromServerAsync(CancellationToken ct = default);
}
