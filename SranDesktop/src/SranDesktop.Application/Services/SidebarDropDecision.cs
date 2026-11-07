namespace SranDesktop.Application.Services;

public enum SidebarDropTargetKind
{
    /// <summary>Dropped onto a project header row.</summary>
    Project,
    /// <summary>Dropped onto the "remove from project" space at the list top.</summary>
    Unfile
}

/// <summary>Outcome of a sidebar drop: move (with the new project id, null =
/// unfile) or no-op.</summary>
public sealed record SidebarDropResult(bool ShouldMove, string? TargetProjectId)
{
    public static readonly SidebarDropResult NoOp = new(false, null);
}

/// <summary>
/// Pure drop policy for sidebar drag-drop (Phase 8 Task 2). Only conversation
/// rows are draggable (projects are not, this phase), so the inputs are the
/// dragged CONVERSATION's state plus the target. Dropping a chat onto the
/// project it is already in, or unfiling an already-unfiled chat, is a no-op —
/// no server round-trip, no store write.
/// </summary>
public static class SidebarDropDecision
{
    public static SidebarDropResult Decide(
        string draggedConversationId,
        string? draggedCurrentProjectId,
        SidebarDropTargetKind targetKind,
        string? targetProjectId)
    {
        if (string.IsNullOrEmpty(draggedConversationId))
            return SidebarDropResult.NoOp;

        if (targetKind == SidebarDropTargetKind.Project)
        {
            if (string.IsNullOrEmpty(targetProjectId))
                return SidebarDropResult.NoOp;
            if (draggedCurrentProjectId == targetProjectId)
                return SidebarDropResult.NoOp; // already filed there
            return new SidebarDropResult(true, targetProjectId);
        }

        // Unfile target: only meaningful for a chat currently in a project.
        return draggedCurrentProjectId is null
            ? SidebarDropResult.NoOp
            : new SidebarDropResult(true, null);
    }
}
