using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>
/// Replaces old screenshot vision messages with a small text placeholder so a
/// long computer-use session doesn't re-upload every historical screenshot on
/// every iteration. The message is replaced, not removed — removing it would
/// confuse the model's narrative of what it did. Idempotent: pruned messages
/// are plain strings and no longer match the detector.
/// </summary>
public static class ScreenshotPruner
{
    public const string PrunedPlaceholder =
        "[An earlier screenshot was removed to save context space. " +
        "Take a new screenshot if you need to see the current state.]";

    /// <summary>
    /// Maps a model's context window to how many recent screenshots to keep in
    /// the main agent loop (Phase 14 B). Each screenshot costs ~1100 tokens
    /// (see <see cref="TokenEstimator"/>), so even the ceiling of 4 is only a
    /// few thousand tokens — a small fraction of a large window — while giving
    /// the model more recent visual context to reason over. Small windows keep
    /// just the latest two to leave room for the conversation itself. The 1–5
    /// clamp is a guardrail so an odd catalog value can never keep zero (the
    /// model would lose all visual state) or an unbounded number.
    /// </summary>
    public static int KeepLatestFor(int contextWindow)
    {
        var keep = contextWindow switch
        {
            < 50_000 => 2,
            < 200_000 => 3,
            _ => 4,
        };
        return Math.Clamp(keep, 1, 5);
    }

    /// <summary>Prunes all screenshot messages except the latest
    /// <paramref name="keepLatest"/>. Returns whether anything was pruned.</summary>
    public static bool Prune(List<GatewayMessage> messages, int keepLatest = 2)
    {
        var screenshotIndexes = new List<int>();
        for (var i = 0; i < messages.Count; i++)
        {
            if (ScreenshotMessage.IsScreenshotMessage(messages[i]))
                screenshotIndexes.Add(i);
        }

        var pruneCount = screenshotIndexes.Count - keepLatest;
        if (pruneCount <= 0)
            return false;

        foreach (var index in screenshotIndexes.Take(pruneCount))
            messages[index] = new GatewayMessage { Role = "user", Content = PrunedPlaceholder };
        return true;
    }
}
