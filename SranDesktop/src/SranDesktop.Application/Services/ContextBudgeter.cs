using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>Outcome of a <see cref="ContextBudgeter.FitToBudget"/> pass.</summary>
public sealed record BudgetResult(bool Trimmed, int OmittedMessages, int EstimatedTokens);

/// <summary>
/// Trims a gateway message list to fit a model's context window. Trimming
/// removes whole conversation "groups" (a plain-text user turn plus everything
/// the assistant did in response — tool calls, tool results, screenshots), so
/// a tool message is never orphaned from the assistant message that carries
/// its tool_call_id. The system prologue and the final group are never removed.
/// </summary>
public static class ContextBudgeter
{
    /// <summary>Fraction of the window usable after the output reserve —
    /// the 10% haircut absorbs TokenEstimator error.</summary>
    private const double SafetyMargin = 0.9;

    public const int DefaultOutputReserve = 8192;

    private const string TrimNotePrefix = "[Note: ";
    private const string TrimNoteSuffix = "Ask the user if you are missing needed context.]";

    public static BudgetResult FitToBudget(
        List<GatewayMessage> messages, int contextWindow, int outputReserve = DefaultOutputReserve)
    {
        var budget = (int)((contextWindow - outputReserve) * SafetyMargin);
        var estimate = TokenEstimator.EstimateMessages(messages);
        if (estimate <= budget)
            return new BudgetResult(false, 0, estimate);

        var prologueEnd = 0;
        while (prologueEnd < messages.Count && messages[prologueEnd].Role == "system")
            prologueEnd++;

        // A note from an earlier pass is absorbed and re-inserted with a
        // cumulative count — never treated as a removable group of its own.
        var omitted = 0;
        if (prologueEnd < messages.Count && IsTrimNote(messages[prologueEnd]))
        {
            omitted = ParseOmittedCount(messages[prologueEnd]);
            messages.RemoveAt(prologueEnd);
        }

        // Remove whole groups, oldest first, until under budget or only the
        // final group remains.
        var removedGroups = false;
        while (true)
        {
            if (TokenEstimator.EstimateMessages(messages) <= budget)
                break;
            var starts = GroupStarts(messages, prologueEnd);
            if (starts.Count <= 1)
                break; // only the final group (or nothing groupable) left
            var length = starts[1] - starts[0];
            messages.RemoveRange(starts[0], length);
            omitted += length;
            removedGroups = true;
        }

        if (omitted > 0)
            messages.Insert(prologueEnd, new GatewayMessage
            {
                Role = "user",
                Content = BuildTrimNote(omitted)
            });

        // Monster final turn: groups alone couldn't get under budget —
        // sacrifice all but the newest screenshot and accept the rest (the
        // caller's context-error retry handles a provider rejection).
        var prunedScreenshots = false;
        estimate = TokenEstimator.EstimateMessages(messages);
        if (estimate > budget)
        {
            prunedScreenshots = ScreenshotPruner.Prune(messages, keepLatest: 1);
            estimate = TokenEstimator.EstimateMessages(messages);
        }

        return new BudgetResult(
            removedGroups || prunedScreenshots || omitted > 0,
            omitted,
            estimate);
    }

    /// <summary>Indexes of group heads: every plain-string user message after
    /// the system prologue. Multimodal user messages (screenshots, attached
    /// images) belong to the group of the preceding real user turn.</summary>
    private static List<int> GroupStarts(List<GatewayMessage> messages, int prologueEnd)
    {
        var starts = new List<int>();
        for (var i = prologueEnd; i < messages.Count; i++)
        {
            if (messages[i].Role == "user" && messages[i].Content is string)
                starts.Add(i);
        }
        return starts;
    }

    private static string BuildTrimNote(int omitted) =>
        $"{TrimNotePrefix}{omitted} earlier messages in this conversation were omitted " +
        "because the conversation exceeded the model's context window. " +
        TrimNoteSuffix;

    private static bool IsTrimNote(GatewayMessage message) =>
        message.Role == "user"
        && message.Content is string text
        && text.StartsWith(TrimNotePrefix, StringComparison.Ordinal)
        && text.EndsWith(TrimNoteSuffix, StringComparison.Ordinal);

    private static int ParseOmittedCount(GatewayMessage note)
    {
        var text = (string)note.Content!;
        var digits = text.Skip(TrimNotePrefix.Length).TakeWhile(char.IsDigit).ToArray();
        return int.TryParse(new string(digits), out var n) ? n : 0;
    }
}
