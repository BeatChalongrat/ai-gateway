namespace SranDesktop.Domain.Entities;

public sealed class ToolExecution
{
    public required string Id { get; init; }
    public required string ConversationId { get; init; }
    public string? MessageId { get; init; }
    public required string ToolName { get; init; }
    public required string ArgumentsJson { get; init; }
    public string? Result { get; set; }

    /// <summary>How the execution was authorized: auto | allowed | denied.</summary>
    public required string Permission { get; set; }

    public long? DurationMs { get; set; }
    public string? Error { get; set; }
    public required DateTimeOffset ExecutedAt { get; init; }
}
