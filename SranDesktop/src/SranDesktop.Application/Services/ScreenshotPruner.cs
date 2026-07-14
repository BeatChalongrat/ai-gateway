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
