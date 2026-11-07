using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

// Wire shapes of chat-edge KB management endpoints (internal/handler/knowledge.go).
// The lightweight KnowledgeBaseDto in ChatEdgeModels.cs stays as the composer
// popup's shape; these carry the full management fields.

public sealed record KnowledgeBaseDetailDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("description")] string? Description,
    [property: JsonPropertyName("chunk_size")] int ChunkSize,
    [property: JsonPropertyName("chunk_overlap")] int ChunkOverlap,
    [property: JsonPropertyName("is_active")] bool IsActive,
    [property: JsonPropertyName("document_count")] int DocumentCount,
    [property: JsonPropertyName("total_chunks")] int TotalChunks,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt);

public sealed record KnowledgeBaseDetailListResponse(
    [property: JsonPropertyName("knowledge_bases")] List<KnowledgeBaseDetailDto> KnowledgeBases);

/// <summary>GET /knowledge-bases/{id} — KB plus its documents.</summary>
public sealed record KnowledgeBaseDetailResponse(
    [property: JsonPropertyName("knowledge_base")] KnowledgeBaseDetailDto KnowledgeBase,
    [property: JsonPropertyName("documents")] List<KbDocumentDto> Documents);

public sealed record KbDocumentDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("knowledge_base_id")] string? KnowledgeBaseId,
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("content_type")] string? ContentType,
    [property: JsonPropertyName("file_size_bytes")] long FileSizeBytes,
    // pending | processing | indexed | failed
    [property: JsonPropertyName("status")] string? Status,
    [property: JsonPropertyName("chunk_count")] int ChunkCount,
    [property: JsonPropertyName("char_count")] int CharCount,
    [property: JsonPropertyName("error_message")] string? ErrorMessage,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt);
