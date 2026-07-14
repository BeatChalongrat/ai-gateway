using SranDesktop.Application.Models;

namespace SranDesktop.Application.Interfaces;

/// <summary>
/// Typed client for chat-edge-api (:4000/api/v1). Authenticated with the Portal
/// access JWT as an Authorization: Bearer header (attached by AuthTokenHandler).
/// Grows with each build step.
/// </summary>
public interface IChatEdgeApiClient
{
    Task<ConversationListResponse> ListConversationsAsync(
        string? cursor = null, int limit = 50, CancellationToken ct = default);

    Task<ConversationListResponse> SearchConversationsAsync(
        string query, int limit = 50, CancellationToken ct = default);

    Task<ConversationDto> CreateConversationAsync(string modelId, CancellationToken ct = default);

    /// <summary>Fresh server state for one conversation (model / KB / memory / pin).</summary>
    Task<ConversationDto> GetConversationAsync(string id, CancellationToken ct = default);

    Task<ConversationDto> RenameConversationAsync(string id, string title, CancellationToken ct = default);

    Task<ConversationDto> SetConversationPinnedAsync(string id, bool pinned, CancellationToken ct = default);

    /// <summary>Reassign the model used for this conversation's future turns.</summary>
    Task<ConversationDto> SetConversationModelAsync(string id, string modelId, CancellationToken ct = default);

    /// <summary>Bind (or, with null, unbind) a knowledge base for RAG on this conversation.</summary>
    Task<ConversationDto> SetConversationKnowledgeBaseAsync(string id, string? knowledgeBaseId, CancellationToken ct = default);

    /// <summary>Toggle long-term memory capture/use for this conversation.</summary>
    Task<ConversationDto> SetConversationMemoryAsync(string id, bool enabled, CancellationToken ct = default);

    Task DeleteConversationAsync(string id, CancellationToken ct = default);

    // ── Desktop agent-conversation backup mirror (Phase 3 push sync) ────────

    /// <summary>Create-or-get the server mirror of a local agent conversation
    /// (idempotent by client_id). Returns the SERVER conversation + created flag.</summary>
    Task<ImportConversationResponse> ImportConversationAsync(
        string clientId, string? title, string? modelId, DateTimeOffset createdAt,
        CancellationToken ct = default);

    /// <summary>Append historical messages to the mirror without generation.
    /// Idempotent by per-message client_id; ≤200 messages per call.</summary>
    Task<ImportMessagesResponse> ImportMessagesAsync(
        string serverConversationId, IReadOnlyList<ImportMessageItem> messages,
        CancellationToken ct = default);

    /// <summary>The caller's chat folders, ordered by position.</summary>
    Task<FolderListResponse> ListFoldersAsync(CancellationToken ct = default);

    Task<FolderDto> CreateFolderAsync(string name, CancellationToken ct = default);

    Task<FolderDto> RenameFolderAsync(string id, string name, CancellationToken ct = default);

    /// <summary>Deletes a folder; its conversations become unfiled server-side.</summary>
    Task DeleteFolderAsync(string id, CancellationToken ct = default);

    /// <summary>Files a conversation into a folder, or unfiles it when <paramref name="folderId"/> is null.</summary>
    Task MoveConversationToFolderAsync(string conversationId, string? folderId, CancellationToken ct = default);

    Task<ModelListResponse> ListModelsAsync(CancellationToken ct = default);

    /// <summary>Caller's credit balance + per-model usage breakdown.</summary>
    Task<UsageResponse> GetUsageAsync(CancellationToken ct = default);

    /// <summary>Tenant-scoped knowledge bases available to bind to a conversation.</summary>
    Task<KnowledgeBaseListResponse> ListKnowledgeBasesAsync(CancellationToken ct = default);

    /// <summary>Server-rendered transcript export. <paramref name="format"/> is "markdown" or "json".</summary>
    Task<string> ExportConversationAsync(string id, string format, CancellationToken ct = default);

    /// <summary>Latest messages of a conversation in ascending order; `before` (a message id) pages older.</summary>
    Task<MessageListResponse> ListMessagesAsync(
        string conversationId, int limit = 50, string? before = null, CancellationToken ct = default);

    /// <summary>
    /// POST {content, stream:true, file_ids?, image_mode?} and stream back raw
    /// SSE frames until the server closes the turn. video_mode is sent for
    /// forward-compat but the current server only reads image_mode.
    /// </summary>
    IAsyncEnumerable<SseFrame> StreamMessageAsync(
        string conversationId, string content, IReadOnlyList<string>? fileIds = null,
        bool imageMode = false, bool videoMode = false,
        CancellationToken ct = default);

    /// <summary>
    /// GET a server file as raw bytes. <paramref name="downloadUrl"/> is the
    /// relative download_url from the server (e.g. "/api/v1/files/{id}/download").
    /// </summary>
    Task<byte[]> DownloadFileAsync(string downloadUrl, CancellationToken ct = default);

    /// <summary>Asks the server to summarize the first turn into a sidebar title and persist it.</summary>
    Task<GenerateTitleResponse> GenerateTitleAsync(string conversationId, CancellationToken ct = default);

    // ── Phase 5: backend wiring ─────────────────────────────────────────────

    /// <summary>Interrupts the in-flight server-side generation for a chat-edge
    /// conversation. Idempotent; returns whether anything was cancelled.</summary>
    Task<bool> CancelStreamAsync(string conversationId, CancellationToken ct = default);

    /// <summary>Upserts thumbs feedback for an ASSISTANT message (server message
    /// ids only). rating = "thumbs_up" | "thumbs_down". No delete route exists.</summary>
    Task SendFeedbackAsync(string messageId, string rating, CancellationToken ct = default);

    /// <summary>Context-compiler dry run for the "chat is getting long" nudge.
    /// Throws on 404/503 — callers treat any failure as "no nudge".</summary>
    Task<ContextInfoDto> GetContextInfoAsync(string conversationId, CancellationToken ct = default);

    /// <summary>Full-text search over message content (scope=messages, limit
    /// capped at 20 server-side). Hits carry no message id — open-conversation only.</summary>
    Task<MessageSearchResponse> SearchMessagesAsync(string query, int limit = 20, CancellationToken ct = default);

    /// <summary>Multipart upload (form field "file"). Progress reports 0–100.</summary>
    Task<FileAttachmentDto> UploadFileAsync(
        string conversationId, Stream content, string fileName, string contentType, long? length,
        IProgress<double>? progress = null, CancellationToken ct = default);

    // ── Plan execution (R7 Feature C) ───────────────────────────────────────

    /// <summary>POST .../excel/execute — the fenced plan JSON goes under "plan".</summary>
    Task<PlanExecutionResult> ExecuteExcelPlanAsync(
        string conversationId, ExecuteExcelPlanRequest request, CancellationToken ct = default);

    /// <summary>POST .../document/execute — docx / pdf / pptx.</summary>
    Task<PlanExecutionResult> ExecuteDocumentPlanAsync(
        string conversationId, ExecuteDocumentPlanRequest request, CancellationToken ct = default);

    // ── Knowledge base management (R7 Feature D) ────────────────────────────

    Task<KnowledgeBaseDetailListResponse> ListKnowledgeBasesDetailedAsync(CancellationToken ct = default);

    Task<KnowledgeBaseDetailDto> CreateKnowledgeBaseAsync(
        string name, string? description, int chunkSize = 512, int chunkOverlap = 50,
        CancellationToken ct = default);

    Task<KnowledgeBaseDetailResponse> GetKnowledgeBaseAsync(string id, CancellationToken ct = default);

    Task<KnowledgeBaseDetailDto> UpdateKnowledgeBaseAsync(
        string id, string? name = null, string? description = null, bool? isActive = null,
        CancellationToken ct = default);

    Task DeleteKnowledgeBaseAsync(string id, CancellationToken ct = default);

    /// <summary>Multipart upload (form field "file") into a knowledge base.</summary>
    Task<KbDocumentDto> UploadKbDocumentAsync(
        string kbId, Stream content, string fileName, string contentType, long? length,
        IProgress<double>? progress = null, CancellationToken ct = default);

    Task DeleteKbDocumentAsync(string kbId, string docId, CancellationToken ct = default);

    Task<KbDocumentDto> ReindexKbDocumentAsync(string kbId, string docId, CancellationToken ct = default);

    // ── Memory management (R7 Feature E) ────────────────────────────────────

    Task<MemoryListResponse> ListMemoriesAsync(
        string? scopeType = null, string? memoryType = null, string? status = null,
        int limit = 100, int offset = 0, CancellationToken ct = default);

    Task<MemoryDto> CreateMemoryAsync(
        string content, string memoryType, string? category = null, CancellationToken ct = default);

    Task<MemoryDto> UpdateMemoryAsync(
        string id, string? content = null, string? memoryType = null, string? category = null,
        CancellationToken ct = default);

    Task DeleteMemoryAsync(string id, CancellationToken ct = default);

    Task<MemoryDto> PinMemoryAsync(string id, bool pinned, CancellationToken ct = default);

    Task<MemoryDto> ConfirmMemoryAsync(string id, bool confirmed, CancellationToken ct = default);

    Task<BulkDeleteResponse> ClearAllMemoriesAsync(CancellationToken ct = default);

    Task<int> GetPendingMemoryCountAsync(CancellationToken ct = default);

    /// <summary>Prepares a server-side export, downloads it, and returns the JSON text.</summary>
    Task<string> ExportMemoriesAsync(CancellationToken ct = default);

    /// <summary>GDPR hard purge. The server requires the exact confirmation phrase.</summary>
    Task PurgeMemoriesAsync(CancellationToken ct = default);

    // ── Sharing (R9 Feature K) ──────────────────────────────────────────────

    /// <summary>Create (or return the existing) public share link for a conversation.</summary>
    Task<ShareResponse> ShareConversationAsync(string id, CancellationToken ct = default);

    Task RevokeShareAsync(string id, CancellationToken ct = default);
}
