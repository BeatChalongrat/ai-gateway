using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

public sealed record ConversationDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("model_id")] string? ModelId,
    [property: JsonPropertyName("message_count")] int MessageCount,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt,
    [property: JsonPropertyName("is_pinned")] bool IsPinned,
    [property: JsonPropertyName("folder_id")] string? FolderId,
    [property: JsonPropertyName("knowledge_base_id")] string? KnowledgeBaseId = null,
    // Absent ⇒ treat as enabled (matches web's default-on behaviour).
    [property: JsonPropertyName("memory_enabled")] bool? MemoryEnabled = null);

public sealed record ConversationListResponse(
    [property: JsonPropertyName("conversations")] List<ConversationDto> Conversations,
    [property: JsonPropertyName("has_more")] bool HasMore,
    [property: JsonPropertyName("next_cursor")] string? NextCursor);

public sealed record CreateConversationRequest(
    [property: JsonPropertyName("model_id")] string ModelId);

public sealed record MessageDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("content")] string? Content,
    [property: JsonPropertyName("status")] string? Status,
    [property: JsonPropertyName("model_id")] string? ModelId,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("token_usage")] TokenUsagePayload? TokenUsage,
    // FIX 9: the desktop's own message id for rows this client imported —
    // correlates a server row back to its local copy (dedup + tool-chain
    // re-attachment). Null for native chat-edge rows.
    [property: JsonPropertyName("client_id")] string? ClientId = null,
    // FIX 10: the message's file attachments (uploads + generated media),
    // embedded per message by the server's list endpoint. Null/empty when
    // the message has none.
    [property: JsonPropertyName("files")] List<FileAttachmentDto>? Files = null);

/// <summary>GET /conversations/{id}/messages — latest N in ascending order; `before` pages older.</summary>
public sealed record MessageListResponse(
    [property: JsonPropertyName("has_more")] bool HasMore,
    [property: JsonPropertyName("messages")] List<MessageDto> Messages);

public sealed record GenerateTitleResponse(
    [property: JsonPropertyName("title")] string Title);

public sealed record FileAttachmentDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("conversation_id")] string? ConversationId,
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("content_type")] string? ContentType,
    [property: JsonPropertyName("file_size_bytes")] long FileSizeBytes,
    [property: JsonPropertyName("download_url")] string? DownloadUrl,
    [property: JsonPropertyName("extraction_status")] string? ExtractionStatus,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    // FIX 10: THIS device's deterministic local file id when the file was
    // uploaded by this desktop (dedup key echo); null for web uploads and
    // generated files. Correlates a server file to its local manifest row.
    [property: JsonPropertyName("client_id")] string? ClientId = null);

/// <summary>
/// GET /files/{id}/text — the text chat-edge extracted from an attachment.
/// <see cref="Text"/> is only populated once <see cref="ExtractionStatus"/> is
/// "completed"; a non-terminal status means "still working, poll again" and
/// must never be read as "this document is blank".
/// </summary>
public sealed record FileTextDto(
    [property: JsonPropertyName("extraction_status")] string ExtractionStatus,
    [property: JsonPropertyName("text")] string? Text,
    [property: JsonPropertyName("truncated")] bool Truncated,
    [property: JsonPropertyName("ocr")] bool Ocr);

public sealed record ReasoningCapability(
    [property: JsonPropertyName("supported")] bool Supported,
    [property: JsonPropertyName("modes")] List<string>? Modes,
    [property: JsonPropertyName("default_mode")] string? DefaultMode,
    [property: JsonPropertyName("summary_supported")] bool SummarySupported);

public sealed record ModelInfo(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("provider")] string? Provider,
    [property: JsonPropertyName("model_kind")] string? ModelKind,
    [property: JsonPropertyName("allowed")] bool Allowed,
    [property: JsonPropertyName("description")] string? Description = null,
    [property: JsonPropertyName("context_window")] int? ContextWindow = null,
    [property: JsonPropertyName("capabilities")] List<string>? Capabilities = null,
    [property: JsonPropertyName("category")] string? Category = null,
    [property: JsonPropertyName("icon")] string? Icon = null,
    [property: JsonPropertyName("input_price_per_1m")] double? InputPricePer1M = null,
    [property: JsonPropertyName("output_price_per_1m")] double? OutputPricePer1M = null,
    [property: JsonPropertyName("reasoning")] ReasoningCapability? Reasoning = null)
{
    /// <summary>Label for pickers: the friendly name, falling back to the raw id.</summary>
    [JsonIgnore]
    public string DisplayName => string.IsNullOrEmpty(Name) ? Id : Name!;

    [JsonIgnore]
    public bool SupportsImage => Capabilities?.Any(c =>
        c is "image_generation_via_tools" or "image_generation") == true;

    [JsonIgnore]
    public bool SupportsVideo => Capabilities?.Any(c =>
        c is "video_generation" or "video_generation_via_tools") == true;

    [JsonIgnore]
    public bool SupportsAudio => Capabilities?.Any(c =>
        c is "audio_generation" or "tts") == true;

    [JsonIgnore]
    public bool SupportsThinking => Reasoning?.Supported == true
        || Capabilities?.Contains("reasoning") == true;

    /// <summary>Accepts image INPUT (multimodal analysis), not generation (R9 D).</summary>
    [JsonIgnore]
    public bool SupportsVision => Capabilities?.Any(c =>
        c is "vision" or "image_input" or "multimodal") == true;
}

public sealed record ModelListResponse(
    [property: JsonPropertyName("models")] List<ModelInfo> Models);

// ── Usage (GET /api/v1/usage/me) ────────────────────────────────────────────

public sealed record UsageCredits(
    [property: JsonPropertyName("total")] long Total,
    [property: JsonPropertyName("used")] long Used,
    [property: JsonPropertyName("remaining")] long Remaining);

public sealed record UsageByModelItem(
    [property: JsonPropertyName("model")] string Model,
    [property: JsonPropertyName("provider")] string? Provider,
    [property: JsonPropertyName("tokens")] long Tokens,
    [property: JsonPropertyName("credits_used")] long CreditsUsed,
    [property: JsonPropertyName("request_count")] long RequestCount,
    [property: JsonPropertyName("percentage")] double Percentage);

public sealed record UsageResponse(
    [property: JsonPropertyName("credits")] UsageCredits Credits,
    [property: JsonPropertyName("usage_by_model")] List<UsageByModelItem> UsageByModel,
    [property: JsonPropertyName("total_credits_used")] long TotalCreditsUsed,
    [property: JsonPropertyName("period")] string? Period);

public sealed record FolderDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("position")] int Position,
    [property: JsonPropertyName("conversation_count")] int ConversationCount,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt);

public sealed record FolderListResponse(
    [property: JsonPropertyName("folders")] List<FolderDto> Folders);

public sealed record KnowledgeBaseDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("document_count")] int DocumentCount,
    [property: JsonPropertyName("total_chunks")] int TotalChunks);

public sealed record KnowledgeBaseListResponse(
    [property: JsonPropertyName("knowledge_bases")] List<KnowledgeBaseDto> KnowledgeBases);

// ── Desktop import (Phase 3 push sync) — mirrors chat-edge handler/import.go ──

public sealed record ImportConversationRequest(
    [property: JsonPropertyName("client_id")] string ClientId,
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("model_id")] string? ModelId,
    // RFC3339; empty/null lets the server stamp NOW().
    [property: JsonPropertyName("created_at")] string? CreatedAt);

public sealed record ImportConversationResponse(
    [property: JsonPropertyName("conversation")] ConversationDto Conversation,
    [property: JsonPropertyName("created")] bool Created);

public sealed record ImportMessageItem(
    [property: JsonPropertyName("client_id")] string ClientId,
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("content")] string Content,
    [property: JsonPropertyName("model_id")] string? ModelId,
    // RFC3339, required — historical data keeps its original timestamp.
    [property: JsonPropertyName("created_at")] string CreatedAt,
    // FIX 10: server file ids to link to this message (the agent path's
    // equivalent of the send path's file_ids). Omitted when none.
    [property: JsonPropertyName("file_ids")] List<string>? FileIds = null);

public sealed record ImportMessagesRequest(
    [property: JsonPropertyName("messages")] List<ImportMessageItem> Messages);

public sealed record ImportMessagesResponse(
    [property: JsonPropertyName("imported")] int Imported,
    [property: JsonPropertyName("skipped")] int Skipped);

// ── Phase 5: cancel / context info / message search ──────────────────────────

public sealed record CancelStreamResponse(
    [property: JsonPropertyName("cancelled")] bool Cancelled);

/// <summary>GET /conversations/{id}/context-info — S4 context-compiler dry run.
/// 503 when the service isn't wired; callers treat any failure as "no data".</summary>
public sealed record ContextInfoDto(
    [property: JsonPropertyName("conversation_id")] string ConversationId,
    [property: JsonPropertyName("total_budget")] int TotalBudget,
    [property: JsonPropertyName("system_prompt_tokens")] int SystemPromptTokens,
    [property: JsonPropertyName("output_reserve")] int OutputReserve,
    [property: JsonPropertyName("tool_reserve")] int ToolReserve,
    [property: JsonPropertyName("messages_budget")] int MessagesBudget,
    [property: JsonPropertyName("messages_total")] int MessagesTotal,
    [property: JsonPropertyName("messages_would_send")] int MessagesWouldSend,
    [property: JsonPropertyName("compacted")] bool Compacted,
    [property: JsonPropertyName("extraction_triggered")] bool ExtractionTriggered);

/// <summary>One hit from GET /search?scope=messages. NOTE: the server sends no
/// message_id — a hit can only open its conversation, never jump to the row.</summary>
public sealed record MessageSearchHitDto(
    [property: JsonPropertyName("type")] string? Type,
    [property: JsonPropertyName("conversation_id")] string ConversationId,
    [property: JsonPropertyName("conversation_title")] string? ConversationTitle,
    [property: JsonPropertyName("content")] string? Content,
    [property: JsonPropertyName("role")] string? Role,
    [property: JsonPropertyName("date")] string? Date,
    [property: JsonPropertyName("relevance")] string? Relevance);

public sealed record MessageSearchResponse(
    [property: JsonPropertyName("query")] string? Query,
    [property: JsonPropertyName("scope")] string? Scope,
    [property: JsonPropertyName("results")] List<MessageSearchHitDto>? Results,
    [property: JsonPropertyName("total")] int Total);

/// <summary>POST /conversations/{id}/share (R9 K).</summary>
public sealed record ShareResponse(
    [property: JsonPropertyName("share_token")] string ShareToken,
    [property: JsonPropertyName("share_url")] string ShareUrl,
    [property: JsonPropertyName("expires_at")] DateTimeOffset? ExpiresAt,
    [property: JsonPropertyName("created_at")] DateTimeOffset? CreatedAt);
