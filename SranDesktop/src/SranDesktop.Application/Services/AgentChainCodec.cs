using System.Text.Json;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Application.Services;

/// <summary>
/// Durable tool history (Phase A PART 1). Converts between the live gateway
/// message chain an agent turn produces (assistant messages carrying
/// tool_calls, role:"tool" results, screenshot vision messages) and the
/// persisted <see cref="Message"/> rows that survive conversation switches and
/// restarts, and regroups those rows back into per-assistant chains on reload.
///
/// The <c>messages</c> table already has the tool_calls / tool_call_id / name
/// columns (baseline schema), so no migration is needed — this is the pure
/// mapping that puts them to use. Screenshots are stored as SMALL placeholders
/// (marked with <see cref="ScreenshotMarker"/> in the row's name column), never
/// raw base64: old screenshots are pruned to placeholders anyway (Phase 2
/// ScreenshotPruner), so a stored placeholder is lossless relative to what the
/// next turn would send.
/// </summary>
public static class AgentChainCodec
{
    /// <summary>Row-name marker distinguishing a persisted screenshot placeholder
    /// (role user) from a real user message, so reload never mistakes one for a
    /// conversation turn.</summary>
    public const string ScreenshotMarker = "__screenshot__";

    public const string ScreenshotPlaceholderText =
        "[A screenshot was captured here earlier; it is no longer shown. " +
        "Take a new screenshot if you need to see the current screen.]";

    private static readonly JsonSerializerOptions ToolCallJson = new();

    /// <summary>
    /// Returns the chain with screenshot vision messages collapsed to small text
    /// placeholders — used before persisting AND before stashing the chain on a
    /// bubble for future turns. The live list handed to a running/resuming loop
    /// is NOT sanitized (it keeps real screenshots so the model can still see
    /// them mid-task).
    /// </summary>
    public static List<GatewayMessage> Sanitize(IEnumerable<GatewayMessage> chain)
    {
        var result = new List<GatewayMessage>();
        foreach (var msg in chain)
        {
            result.Add(ScreenshotMessage.IsScreenshotMessage(msg)
                ? new GatewayMessage { Role = "user", Content = ScreenshotPlaceholderText }
                : msg);
        }
        return result;
    }

    /// <summary>
    /// Converts a (already sanitized) chain to persistable rows with stable,
    /// deterministic ids keyed to the owning assistant bubble, so re-persisting
    /// after a Continue upserts the same rows instead of duplicating them.
    /// </summary>
    public static List<Message> ToRows(
        string conversationId, string assistantId,
        IReadOnlyList<GatewayMessage> chain, DateTimeOffset baseTime, int baseSort)
    {
        var rows = new List<Message>(chain.Count);
        for (var i = 0; i < chain.Count; i++)
        {
            var msg = chain[i];
            var createdAt = baseTime.AddMilliseconds(i + 1);
            var id = $"{assistantId}-tc{i}";

            if (msg.Role == "tool")
            {
                rows.Add(new Message
                {
                    Id = id, ConversationId = conversationId, Role = MessageRole.Tool,
                    Content = msg.Content as string,
                    ToolCallId = msg.ToolCallId, Name = msg.Name,
                    CreatedAt = createdAt, SortOrder = baseSort + i
                });
            }
            else if (msg.Role == "assistant")
            {
                rows.Add(new Message
                {
                    Id = id, ConversationId = conversationId, Role = MessageRole.Assistant,
                    Content = msg.Content as string,
                    ToolCallsJson = msg.ToolCalls is { Count: > 0 }
                        ? JsonSerializer.Serialize(msg.ToolCalls, ToolCallJson)
                        : null,
                    CreatedAt = createdAt, SortOrder = baseSort + i
                });
            }
            else // user — a screenshot placeholder (real user turns aren't in a chain)
            {
                rows.Add(new Message
                {
                    Id = id, ConversationId = conversationId, Role = MessageRole.User,
                    Content = msg.Content as string ?? ScreenshotPlaceholderText,
                    Name = ScreenshotMarker,
                    CreatedAt = createdAt, SortOrder = baseSort + i
                });
            }
        }
        return rows;
    }

    /// <summary>True when a persisted row is an intermediate tool-chain row (an
    /// assistant preamble carrying tool_calls, a tool result, or a screenshot
    /// placeholder) rather than a user/assistant conversation bubble.</summary>
    public static bool IsChainRow(Message row) =>
        row.Role == MessageRole.Tool
        || (row.Role == MessageRole.Assistant && !string.IsNullOrEmpty(row.ToolCallsJson))
        || (row.Role == MessageRole.User && row.Name == ScreenshotMarker);

    /// <summary>Reconstructs a single gateway message from a persisted chain row.</summary>
    public static GatewayMessage FromRow(Message row)
    {
        if (row.Role == MessageRole.Tool)
            return new GatewayMessage
            {
                Role = "tool", Content = row.Content,
                ToolCallId = row.ToolCallId, Name = row.Name
            };

        if (row.Role == MessageRole.User) // screenshot placeholder
            return new GatewayMessage { Role = "user", Content = row.Content ?? ScreenshotPlaceholderText };

        // assistant preamble with tool_calls
        List<GatewayToolCall>? calls = null;
        if (!string.IsNullOrEmpty(row.ToolCallsJson))
        {
            try { calls = JsonSerializer.Deserialize<List<GatewayToolCall>>(row.ToolCallsJson); }
            catch (JsonException) { calls = null; }
        }
        return new GatewayMessage
        {
            Role = "assistant",
            Content = row.Content,
            ToolCalls = calls
        };
    }

    /// <summary>
    /// Regroups persisted rows (ordered by sort_order) into per-assistant tool
    /// chains, keyed by the final-answer assistant row's id. A chain accumulates
    /// across tool-chain rows and is flushed onto the next plain assistant row;
    /// a real user row starts a new turn (any orphan chain from an unfinished
    /// prior turn is dropped from UI attachment — the rows still live in the
    /// store).
    /// </summary>
    public static Dictionary<string, List<GatewayMessage>> GroupChains(IReadOnlyList<Message> rows)
    {
        var map = new Dictionary<string, List<GatewayMessage>>(StringComparer.Ordinal);
        var pending = new List<GatewayMessage>();
        foreach (var row in rows)
        {
            if (IsChainRow(row))
            {
                pending.Add(FromRow(row));
            }
            else if (row.Role == MessageRole.Assistant)
            {
                if (pending.Count > 0)
                {
                    map[row.Id] = pending;
                    pending = new List<GatewayMessage>();
                }
            }
            else // user / system — a new turn boundary
            {
                pending = new List<GatewayMessage>();
            }
        }
        return map;
    }
}
