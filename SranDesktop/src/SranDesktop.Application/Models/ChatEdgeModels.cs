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
    [property: JsonPropertyName("project_id")] string? ProjectId,
    [property: JsonPropertyName("knowledge_base_id")] string? KnowledgeBaseId = null,
    // Absent ⇒ treat as enabled (matches web's default-on behaviour).
    [property: JsonPropertyName("memory_enabled")] bool? MemoryEnabled = null,
    // Phase 13 — Thinking Mode. Absent ⇒ off, which is the server default
    // (migration 019); "off" also means the gateway picks Auto Thinking.
    [property: JsonPropertyName("reasoning_enabled")] bool? ReasoningEnabled = null,
    // Phase 14 — archived (chat-edge migration 032). Absent ⇒ not archived.
    [property: JsonPropertyName("is_archived")] bool IsArchived = false);

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
    [property: JsonPropertyName("files")] List<FileAttachmentDto>? Files = null,
    // Batch 1 D2: web-search citations persisted on the row (server column
    // search_sources, migration 023), embedded per message by the list
    // endpoint. Null/absent when the turn used no web search.
    [property: JsonPropertyName("search_sources")] List<SearchSourceDto>? SearchSources = null);

/// <summary>One persisted web-search citation (D2) — the JSONB entry shape
/// the server stores ({title,url,snippet,domain,published_date}).</summary>
public sealed record SearchSourceDto(
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("url")] string? Url,
    [property: JsonPropertyName("snippet")] string? Snippet,
    [property: JsonPropertyName("domain")] string? Domain,
    [property: JsonPropertyName("published_date")] string? PublishedDate = null);

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

// ---------------------------------------------------------------------------
// GET /files/{id}/preview-data — structured preview (Phase 16).
//
// The companion to FileTextDto: that one carries the flat text the MODEL
// reads, these carry the structure a PERSON reads — a spreadsheet as
// sheets and cells, Word as ordered blocks, a deck slide by slide.
// Shapes mirror services/chat-edge-api/scripts/preview_data.py exactly,
// and the web client's src/lib/previewData.ts mirrors the same thing, so
// both clients render identical content from one producer.
// ---------------------------------------------------------------------------

/// <summary>One paragraph-level element: a heading, a paragraph, a bullet,
/// or a table. <c>Rows</c> is populated only for <c>type == "table"</c>.</summary>
public sealed record PreviewBlockDto(
    [property: JsonPropertyName("type")] string? Type,
    [property: JsonPropertyName("level")] int Level,
    [property: JsonPropertyName("text")] string? Text,
    [property: JsonPropertyName("rows")] List<List<string>>? Rows);

/// <summary>A window of ONE sheet, plus the names of every sheet so the
/// client can draw the tab strip without a second request.</summary>
public sealed record SheetPreviewDto(
    [property: JsonPropertyName("names")] List<string>? Names,
    [property: JsonPropertyName("index")] int Index,
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("columns")] List<string>? Columns,
    [property: JsonPropertyName("rows")] List<List<string>>? Rows,
    [property: JsonPropertyName("offset")] int Offset,
    [property: JsonPropertyName("total_rows")] int TotalRows,
    [property: JsonPropertyName("has_more")] bool HasMore,
    [property: JsonPropertyName("columns_truncated")] bool ColumnsTruncated);

public sealed record DocumentPreviewDto(
    [property: JsonPropertyName("blocks")] List<PreviewBlockDto>? Blocks,
    [property: JsonPropertyName("offset")] int Offset,
    [property: JsonPropertyName("total_blocks")] int TotalBlocks,
    [property: JsonPropertyName("has_more")] bool HasMore);

public sealed record SlidePreviewDto(
    [property: JsonPropertyName("index")] int Index,
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("blocks")] List<PreviewBlockDto>? Blocks);

public sealed record SlidesPreviewDto(
    [property: JsonPropertyName("slides")] List<SlidePreviewDto>? Slides,
    [property: JsonPropertyName("offset")] int Offset,
    [property: JsonPropertyName("total_slides")] int TotalSlides,
    [property: JsonPropertyName("has_more")] bool HasMore);

/// <summary><c>Kind</c> says which of the three is populated.</summary>
public sealed record FilePreviewDataDto(
    [property: JsonPropertyName("kind")] string? Kind,
    [property: JsonPropertyName("sheet")] SheetPreviewDto? Sheet,
    [property: JsonPropertyName("document")] DocumentPreviewDto? Document,
    [property: JsonPropertyName("slides")] SlidesPreviewDto? Slides);

/// <summary>
/// GET/PUT /user/preferences — ChatGPT-style "custom instructions" that apply
/// to EVERY chat of this account, on the server, so the web app and the
/// desktop see (and edit) the same two fields. Phase 13 Task 1.
/// </summary>
public sealed record UserPreferencesDto(
    [property: JsonPropertyName("custom_instructions_about")] string? CustomInstructionsAbout,
    [property: JsonPropertyName("custom_instructions_respond")] string? CustomInstructionsRespond,
    [property: JsonPropertyName("updated_at")] string? UpdatedAt = null);

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

// Phase 9 — the ONE sidebar organiser (chat-edge migration 031 merged
// folders into projects). Instructions + knowledge base are optional: a
// project carrying neither is pure grouping, i.e. the old folder.
// Note there is no ``position`` — the server lists projects newest-first
// by created_at, so the desktop keeps the server's order verbatim.
public sealed record ProjectDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string? Name,
    [property: JsonPropertyName("instructions")] string? Instructions,
    [property: JsonPropertyName("knowledge_base_id")] string? KnowledgeBaseId,
    [property: JsonPropertyName("conversation_count")] int ConversationCount,
    [property: JsonPropertyName("created_at")] DateTimeOffset CreatedAt,
    [property: JsonPropertyName("updated_at")] DateTimeOffset UpdatedAt);

public sealed record ProjectListResponse(
    [property: JsonPropertyName("projects")] List<ProjectDto> Projects);

/// <summary>Single-project envelope — create / rename answer
/// <c>{"project": {...}}</c>, unlike the old folder routes which returned
/// the row bare.</summary>
public sealed record ProjectEnvelope(
    [property: JsonPropertyName("project")] ProjectDto Project);

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
