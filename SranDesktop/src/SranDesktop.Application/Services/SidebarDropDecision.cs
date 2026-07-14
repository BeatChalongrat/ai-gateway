namespace SranDesktop.Application.Services;

public enum SidebarDropTargetKind
{
    /// <summary>Dropped onto a folder header row.</summary>
    Folder,
    /// <summary>Dropped onto the "remove from folder" space at the list top.</summary>
    Unfile
}

/// <summary>Outcome of a sidebar drop: move (with the new folder id, null =
/// unfile) or no-op.</summary>
public sealed record SidebarDropResult(bool ShouldMove, string? TargetFolderId)
{
    public static readonly SidebarDropResult NoOp = new(false, null);
}

/// <summary>
/// Pure drop policy for sidebar drag-drop (Phase 8 Task 2). Only conversation
/// rows are draggable (folders are not, this phase), so the inputs are the
/// dragged CONVERSATION's state plus the target. Dropping a chat onto the
/// folder it is already in, or unfiling an already-unfiled chat, is a no-op —
/// no server round-trip, no store write.
/// </summary>
public static class SidebarDropDecision
{
    public static SidebarDropResult Decide(
        string draggedConversationId,
        string? draggedCurrentFolderId,
        SidebarDropTargetKind targetKind,
        string? targetFolderId)
    {
        if (string.IsNullOrEmpty(draggedConversationId))
            return SidebarDropResult.NoOp;

        if (targetKind == SidebarDropTargetKind.Folder)
        {
            if (string.IsNullOrEmpty(targetFolderId))
                return SidebarDropResult.NoOp;
            if (draggedCurrentFolderId == targetFolderId)
                return SidebarDropResult.NoOp; // already filed there
            return new SidebarDropResult(true, targetFolderId);
        }

        // Unfile target: only meaningful for a chat currently in a folder.
        return draggedCurrentFolderId is null
            ? SidebarDropResult.NoOp
            : new SidebarDropResult(true, null);
    }
}
