namespace SranDesktop.Presentation.ViewModels;

/// <summary>Time-bucket header ("PINNED", "TODAY", …) as a flat-list row.</summary>
public sealed record SidebarGroupHeaderRow(string Label);

/// <summary>The "In messages" section header (Phase 5 search) as a row.</summary>
public sealed record SidebarSearchHeaderRow;

/// <summary>A conversation rendered INSIDE a folder section — wrapping (rather
/// than reusing the VM directly) lets the flat list give folder members their
/// indent without a second implicit template for the same type.</summary>
public sealed record FolderConversationRow(ConversationItemViewModel Conversation);

/// <summary>
/// Phase 8 Task 2: the sidebar body is ONE virtualized items control, so its
/// heterogeneous content (folder headers, folder members, group headers,
/// conversations, search hits) is flattened into a single row list. Pure —
/// unit-tested for ordering; the view model rebuilds it on collection /
/// property changes.
/// </summary>
public static class SidebarRowsBuilder
{
    public static List<object> Build(
        IReadOnlyList<FolderItemViewModel> folders,
        IReadOnlyList<ConversationItemViewModel> conversations,
        Func<ConversationItemViewModel, bool> matchesSearch,
        IReadOnlyList<MessageSearchHitViewModel> searchHits,
        bool showSearchSection)
    {
        var rows = new List<object>();
        var knownFolderIds = folders.Select(f => f.Id).ToHashSet(StringComparer.Ordinal);

        // 1. Folder sections (folder order = server position order, preserved
        //    by the Folders collection), members newest-first.
        foreach (var folder in folders)
        {
            rows.Add(folder);
            if (!folder.IsExpanded)
                continue;
            foreach (var member in conversations
                         .Where(c => c.FolderId == folder.Id && matchesSearch(c))
                         .OrderByDescending(c => c.UpdatedAt))
                rows.Add(new FolderConversationRow(member));
        }

        // 2. Unfiled conversations in time buckets (Pinned / Today / …).
        //    Unknown folder ids fall back here so a chat never disappears
        //    offline — same contract as the old FilterMainList.
        string? currentGroup = null;
        foreach (var conv in conversations
                     .Where(c => (c.FolderId is null || !knownFolderIds.Contains(c.FolderId))
                                 && matchesSearch(c))
                     .OrderBy(c => c.GroupRank)
                     .ThenByDescending(c => c.UpdatedAt))
        {
            if (conv.GroupKey != currentGroup)
            {
                currentGroup = conv.GroupKey;
                rows.Add(new SidebarGroupHeaderRow(currentGroup));
            }
            rows.Add(conv);
        }

        // 3. Full-text "In messages" section (Phase 5), when active.
        if (showSearchSection)
        {
            rows.Add(new SidebarSearchHeaderRow());
            foreach (var hit in searchHits)
                rows.Add(hit);
        }

        return rows;
    }
}
