using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Services;

public sealed class ConversationSyncService : IConversationSyncService
{
    private const int PageSize = 50;
    private const int MaxPages = 10;

    private readonly IChatEdgeApiClient _api;
    private readonly IConversationStore _store;
    private readonly ILogger<ConversationSyncService> _logger;

    public ConversationSyncService(
        IChatEdgeApiClient api,
        IConversationStore store,
        ILogger<ConversationSyncService> logger)
    {
        _api = api;
        _store = store;
        _logger = logger;
    }

    public async Task<bool> SyncFromServerAsync(CancellationToken ct = default)
    {
        List<ConversationDto> serverConversations;
        bool fetchedAll;
        try
        {
            (serverConversations, fetchedAll) = await FetchAllAsync(ct);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Conversation sync skipped — server unreachable");
            return false;
        }

        // A server conversation whose id equals some local conversation's
        // server_id is the backup MIRROR of that local agent conversation
        // (Phase 3 D) — the SAME conversation. The local row stays
        // authoritative; skipping the upsert prevents a duplicate sidebar row.
        var mirrorIds = (await _store.GetConversationsAsync(ct))
            .Where(c => !string.IsNullOrEmpty(c.ServerId))
            .Select(c => c.ServerId!)
            .ToHashSet(StringComparer.Ordinal);

        var now = DateTimeOffset.UtcNow;
        foreach (var dto in serverConversations)
        {
            if (mirrorIds.Contains(dto.Id))
                continue;
            await _store.UpsertConversationAsync(ToEntity(dto, now), ct);
        }

        // Reconcile deletions only when we saw the complete server list —
        // otherwise a partial fetch would wrongly wipe local copies. Local
        // (source='local') rows are NEVER deleted here — the Source check
        // restricts the sweep to server-sourced rows.
        if (fetchedAll)
        {
            var serverIds = serverConversations.Select(c => c.Id).ToHashSet();
            var local = await _store.GetConversationsAsync(ct);
            foreach (var convo in local)
            {
                if (convo.Source == ConversationSource.Server && !serverIds.Contains(convo.Id))
                    await _store.DeleteConversationAsync(convo.Id, ct);
            }
        }

        _logger.LogInformation("Synced {Count} conversations from server (complete={Complete})",
            serverConversations.Count, fetchedAll);
        return true;
    }

    /// <summary>
    /// Fetches BOTH server lists: the active conversations and the archived
    /// ones (Phase 14). Two calls because chat-edge treats the archive as a
    /// separate view — <c>GET /conversations</c> excludes archived rows.
    ///
    /// Both are needed even though only the active ones show in the sidebar.
    /// The caller's deletion reconcile removes any local server-sourced row it
    /// did not see, so fetching only the active list would DELETE the local
    /// copy of every archived chat the moment the user archived it — the exact
    /// opposite of "archive is not delete", and it would empty the archive
    /// view offline.
    ///
    /// The ACTIVE list is required — losing it is a real sync failure. The
    /// ARCHIVE list is best-effort: an older server without the route, or a
    /// blip, must not stop the user's live chats from syncing. When it fails,
    /// the fetch is reported INCOMPLETE, which makes the caller skip the
    /// deletion sweep — so rows we merely failed to see are never deleted.
    /// </summary>
    private async Task<(List<ConversationDto> Items, bool Complete)> FetchAllAsync(CancellationToken ct)
    {
        var (active, activeComplete) = await FetchPagesAsync(
            (cursor, token) => _api.ListConversationsAsync(cursor, PageSize, token), "active", ct);

        List<ConversationDto> archived;
        bool archivedComplete;
        try
        {
            (archived, archivedComplete) = await FetchPagesAsync(
                (cursor, token) => _api.ListArchivedConversationsAsync(cursor, PageSize, token),
                "archived", ct);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning(ex, "Archived conversations could not be fetched; skipping the delete sweep");
            archived = new List<ConversationDto>();
            archivedComplete = false;
        }

        active.AddRange(archived);
        return (active, activeComplete && archivedComplete);
    }

    private async Task<(List<ConversationDto> Items, bool Complete)> FetchPagesAsync(
        Func<string?, CancellationToken, Task<ConversationListResponse>> fetch,
        string label,
        CancellationToken ct)
    {
        var items = new List<ConversationDto>();
        string? cursor = null;

        for (var page = 0; page < MaxPages; page++)
        {
            var response = await fetch(cursor, ct);
            items.AddRange(response.Conversations);

            if (!response.HasMore || string.IsNullOrEmpty(response.NextCursor))
                return (items, true);

            cursor = response.NextCursor;
        }

        _logger.LogWarning(
            "Conversation sync stopped after {Max} pages of the {Label} list; truncated",
            MaxPages, label);
        return (items, false);
    }

    public static Conversation ToEntity(ConversationDto dto, DateTimeOffset syncedAt) => new()
    {
        Id = dto.Id,
        Title = dto.Title,
        ModelId = dto.ModelId,
        CreatedAt = dto.CreatedAt,
        UpdatedAt = dto.UpdatedAt,
        IsPinned = dto.IsPinned,
        IsArchived = dto.IsArchived,
        ProjectId = dto.ProjectId,
        Source = ConversationSource.Server,
        LastSyncedAt = syncedAt
    };
}
