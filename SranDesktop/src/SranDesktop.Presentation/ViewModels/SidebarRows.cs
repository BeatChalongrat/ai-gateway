namespace SranDesktop.Presentation.ViewModels;

/// <summary>Time-bucket header ("PINNED", "TODAY", …) as a flat-list row.</summary>
public sealed record SidebarGroupHeaderRow(string Label);

/// <summary>The "In messages" section header (Phase 5 search) as a row.</summary>
public sealed record SidebarSearchHeaderRow;

/// <summary>A conversation rendered INSIDE a project section — wrapping (rather
/// than reusing the VM directly) lets the flat list give project members their
/// indent without a second implicit template for the same type.</summary>
public sealed record ProjectConversationRow(ConversationItemViewModel Conversation);

/// <summary>
/// Phase 8 Task 2: the sidebar body is ONE virtualized items control, so its
/// heterogeneous content (project headers, project members, group headers,
/// conversations, search hits) is flattened into a single row list. Pure —
/// unit-tested for ordering; the view model rebuilds it on collection /
/// property changes.
/// </summary>
public static class SidebarRowsBuilder
{
    public static List<object> Build(
        IReadOnlyList<ProjectItemViewModel> projects,
        IReadOnlyList<ConversationItemViewModel> conversations,
        Func<ConversationItemViewModel, bool> matchesSearch,
        IReadOnlyList<MessageSearchHitViewModel> searchHits,
        bool showSearchSection)
    {
        var rows = new List<object>();
        var knownProjectIds = projects.Select(f => f.Id).ToHashSet(StringComparer.Ordinal);

        // 1. Project sections (project order = server position order, preserved
        //    by the Projects collection), members newest-first.
        foreach (var project in projects)
        {
            rows.Add(project);
            if (!project.IsExpanded)
                continue;
            foreach (var member in conversations
                         .Where(c => c.ProjectId == project.Id && matchesSearch(c))
                         .OrderByDescending(c => c.UpdatedAt))
                rows.Add(new ProjectConversationRow(member));
        }

        // 2. Unfiled conversations in time buckets (Pinned / Today / …).
        //    Unknown project ids fall back here so a chat never disappears
        //    offline — same contract as the old FilterMainList.
        string? currentGroup = null;
        foreach (var conv in conversations
                     .Where(c => (c.ProjectId is null || !knownProjectIds.Contains(c.ProjectId))
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
