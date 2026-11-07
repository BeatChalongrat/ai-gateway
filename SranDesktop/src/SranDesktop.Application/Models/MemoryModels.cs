using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

// Wire shapes of chat-edge memory endpoints (internal/model/memory.go).

public sealed record MemoryDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("scope_type")] string? ScopeType,
    [property: JsonPropertyName("memory_type")] string? MemoryType,
    [property: JsonPropertyName("category")] string? Category,
    [property: JsonPropertyName("content")] string Content,
    [property: JsonPropertyName("source_type")] string? SourceType,
    [property: JsonPropertyName("status")] string? Status,
    [property: JsonPropertyName("is_user_confirmed")] bool IsUserConfirmed,
    [property: JsonPropertyName("is_pinned")] bool IsPinned,
    [property: JsonPropertyName("use_count")] int UseCount,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt);

public sealed record MemoryListResponse(
    [property: JsonPropertyName("memories")] List<MemoryDto> Memories,
    [property: JsonPropertyName("total")] int Total);

public sealed record PendingCountResponse(
    [property: JsonPropertyName("count")] int Count);

public sealed record BulkDeleteResponse(
    [property: JsonPropertyName("deleted_count")] int DeletedCount);

/// <summary>POST /account/export-memories — export is prepared server-side, then downloaded.</summary>
public sealed record MemoryExportResponse(
    [property: JsonPropertyName("export_id")] string ExportId,
    [property: JsonPropertyName("filename")] string? Filename,
    [property: JsonPropertyName("size_bytes")] long SizeBytes,
    [property: JsonPropertyName("expires_at")] DateTimeOffset? ExpiresAt);
