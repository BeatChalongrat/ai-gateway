namespace SranDesktop.Domain.Entities;

public sealed class FileAttachment
{
    public required string Id { get; init; }
    public required string ConversationId { get; init; }
    public required string Name { get; init; }
    public string? MimeType { get; init; }
    public long SizeBytes { get; init; }
    public string? LocalPath { get; set; }
    public string? ServerUrl { get; set; }
    public required DateTimeOffset CreatedAt { get; init; }
}
