namespace SranDesktop.Application.Models;

/// <summary>
/// Persisted branch fork (R10 Fix 3): <paramref name="Tails"/> holds one
/// ordered message-id list per branch; <paramref name="ActiveIndex"/> is the
/// tail the user last viewed. All messages of every tail live in the normal
/// messages table — this record only encodes which belong to which branch.
/// </summary>
public sealed record BranchSetRecord(
    string ConversationId,
    string BranchKey,
    int ActiveIndex,
    List<List<string>> Tails);
