namespace SranDesktop.Application.Services;

public enum SearchHitAction
{
    /// <summary>A local conversation exists — open it directly.</summary>
    OpenLocal,
    /// <summary>Server-only conversation (never pulled) — fetch and upsert it
    /// locally on click, then open (Phase 8 Task 6).</summary>
    PullThenOpen
}

/// <summary>The action plus the id it applies to: a LOCAL conversation id for
/// <see cref="SearchHitAction.OpenLocal"/>, the SERVER conversation id for
/// <see cref="SearchHitAction.PullThenOpen"/>.</summary>
public sealed record SearchHitResolution(SearchHitAction Action, string ConversationId);

/// <summary>
/// Maps a server-side search hit onto a LOCAL conversation (Phase 5). A hit's
/// conversation_id is always a SERVER id. Resolution order:
///   (a) a local conversation whose Id equals it (server-source rows),
///   (b) a local conversation whose ServerId equals it (Phase 3 mirror of an
///       agent chat) — open the LOCAL conversation, which stays authoritative,
///   (c) no match → PullThenOpen (Phase 8 Task 6): the hit shows normally and
///       clicking pulls the conversation before opening (previously hidden).
/// </summary>
public static class SearchHitResolver
{
    public static SearchHitResolution Resolve(
        string serverConversationId,
        IReadOnlyList<(string Id, string? ServerId)> localConversations)
    {
        var local = ResolveLocalConversationId(serverConversationId, localConversations);
        return local is not null
            ? new SearchHitResolution(SearchHitAction.OpenLocal, local)
            : new SearchHitResolution(SearchHitAction.PullThenOpen, serverConversationId);
    }

    /// <summary>Phase 5 shape: the local conversation id, or null when only a
    /// pull can open the hit. Kept for the existing tests/call sites.</summary>
    public static string? ResolveLocalConversationId(
        string serverConversationId,
        IReadOnlyList<(string Id, string? ServerId)> localConversations)
    {
        foreach (var (id, _) in localConversations)
        {
            if (id == serverConversationId)
                return id;
        }
        foreach (var (id, serverId) in localConversations)
        {
            if (serverId == serverConversationId)
                return id;
        }
        return null;
    }
}
