using System.Text;
using System.Text.Json;
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

    /// <summary>Marker prefix for a per-group structural summary (Phase 14 C).</summary>
    private const string SummaryPrefix = "[Summary of earlier turn: ";

    /// <summary>How many per-group summaries to retain after a trim. The most
    /// recent few carry the freshest dropped context; everything older is still
    /// represented by the cumulative count in the trim note. Bounding this keeps
    /// repeated trims from stacking summaries without limit — each is tiny
    /// (~a couple hundred chars) but the count must not grow unbounded.</summary>
    private const int MaxRetainedSummaries = 3;

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

        // Per-group summaries from an earlier pass are synthetic — their omitted
        // groups are already tallied in the note above. Drop them before the
        // trim loop so they aren't mistaken for removable group heads (they are
        // plain user strings, like a real turn); fresh ones are built below.
        while (prologueEnd < messages.Count && IsGroupSummary(messages[prologueEnd]))
            messages.RemoveAt(prologueEnd);

        // Remove whole groups, oldest first, until under budget or only the
        // final group remains. Each dropped group leaves a deterministic
        // structural summary behind (Phase 14 C) so the gist — the user's ask,
        // the tools run, the files touched — survives the hard drop.
        var removedGroups = false;
        var summaries = new List<string>();
        while (true)
        {
            if (TokenEstimator.EstimateMessages(messages) <= budget)
                break;
            var starts = GroupStarts(messages, prologueEnd);
            if (starts.Count <= 1)
                break; // only the final group (or nothing groupable) left
            var length = starts[1] - starts[0];
            summaries.Add(BuildGroupSummary(messages, starts[0], length));
            messages.RemoveRange(starts[0], length);
            omitted += length;
            removedGroups = true;
        }

        if (omitted > 0)
        {
            // The cumulative note first, then at most the most-recent few
            // summaries (older ones collapse into the note's count). Inserted
            // after the loop — like the note itself — so they never perturb the
            // group detection above; their handful of tokens are covered by the
            // 10% safety margin and reflected in the estimate returned below.
            messages.Insert(prologueEnd, new GatewayMessage
            {
                Role = "user",
                Content = BuildTrimNote(omitted)
            });

            var keep = summaries.Count > MaxRetainedSummaries
                ? summaries.GetRange(summaries.Count - MaxRetainedSummaries, MaxRetainedSummaries)
                : summaries;
            for (var i = 0; i < keep.Count; i++)
                messages.Insert(prologueEnd + 1 + i, new GatewayMessage
                {
                    Role = "user",
                    Content = keep[i]
                });
        }

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

    /// <summary>
    /// Deterministic, LLM-free précis of one conversation group about to be
    /// dropped (Phase 14 C): the user's opening ask (first ~80 chars), the
    /// distinct tools the assistant ran, and any read_file/write_file paths.
    /// Runs inside the synchronous budgeter — an LLM-generated summary is a
    /// future enhancement, deliberately not attempted here.
    /// </summary>
    private static string BuildGroupSummary(List<GatewayMessage> messages, int start, int length)
    {
        string? userAsked = null;
        var toolNames = new List<string>();
        var paths = new List<string>();

        for (var i = start; i < start + length && i < messages.Count; i++)
        {
            var message = messages[i];
            if (userAsked is null && message.Role == "user" && message.Content is string text)
                userAsked = Condense(text);

            if (message.ToolCalls is not { Count: > 0 })
                continue;
            foreach (var call in message.ToolCalls)
            {
                var toolName = call.Function.Name;
                if (!toolNames.Contains(toolName))
                    toolNames.Add(toolName);
                if (toolName is "read_file" or "write_file"
                    && TryExtractPath(call.Function.Arguments) is { } path
                    && !paths.Contains(path))
                    paths.Add(path);
            }
        }

        var sb = new StringBuilder(SummaryPrefix);
        sb.Append("user asked \"").Append(userAsked ?? "(no text)").Append('"');
        if (toolNames.Count > 0)
            sb.Append("; assistant ran tools: ").Append(string.Join(", ", toolNames));
        if (paths.Count > 0)
            sb.Append("; files touched: ").Append(string.Join(", ", paths));
        sb.Append(".]");
        return sb.ToString();
    }

    private static string Condense(string text)
    {
        var oneLine = text.ReplaceLineEndings(" ").Trim();
        return oneLine.Length <= 80 ? oneLine : oneLine[..80] + "…";
    }

    private static string? TryExtractPath(string argumentsJson)
    {
        try
        {
            using var doc = JsonDocument.Parse(
                string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson);
            if (doc.RootElement.ValueKind == JsonValueKind.Object
                && doc.RootElement.TryGetProperty("path", out var path)
                && path.ValueKind == JsonValueKind.String)
                return path.GetString();
        }
        catch (JsonException)
        {
        }
        return null;
    }

    private static bool IsGroupSummary(GatewayMessage message) =>
        message.Role == "user"
        && message.Content is string text
        && text.StartsWith(SummaryPrefix, StringComparison.Ordinal);

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
