using SranDesktop.Domain.Enums;
using SranDesktop.Domain.ValueObjects;

namespace SranDesktop.Domain.Entities;

public sealed class Message
{
    public required string Id { get; init; }
    public required string ConversationId { get; init; }
    public required MessageRole Role { get; init; }
    public string? Content { get; set; }

    /// <summary>Raw JSON array of tool calls (assistant messages in Agent Mode).</summary>
    public string? ToolCallsJson { get; set; }

    /// <summary>Id of the tool call this message answers (role = Tool).</summary>
    public string? ToolCallId { get; init; }

    /// <summary>Tool name (role = Tool).</summary>
    public string? Name { get; init; }

    public string? ModelId { get; set; }
    public TokenUsage? TokenUsage { get; set; }
    public required DateTimeOffset CreatedAt { get; init; }
    public required int SortOrder { get; init; }

    /// <summary>Synced marker (Phase 3 push sync): the SERVER conversation id
    /// this row was pushed to — the server dedupes by our message id as its
    /// client_id, so no per-message server id exists. Null = not yet synced.</summary>
    public string? ServerId { get; set; }

 