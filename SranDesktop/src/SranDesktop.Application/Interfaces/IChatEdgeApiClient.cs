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

    /// <summary>Archive or restore a conversation (Phase 14). Archiving hides
    /// it from the main list without destroying anything — the same PATCH the
    /// web archive view uses.</summary>
    Task<ConversationDto> SetConversationArchivedAsync(string id, bool archived, CancellationToken ct = default);

    /// <summary>The archive view: lists ONLY archived conversations
    /// (<c>GET /conversations?archived=1</c>).</summary>
    Task<ConversationListResponse> ListArchivedConversationsAsync(
        string? cursor = null, int limit = 50, CancellationToken ct = default);

    /// <summary>Turn Thinking Mode on/off for this conversation (Phase 13).
    /// Same PATCH field the web composer's brain button writes, so the state
    /// follows the conversation across clients.</summary>
    Task<ConversationDto> SetConversationReasoningAsync(string id, bool enabled, CancellationToken ct = default);

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

    /// <summary>The caller's chat projects, ordered by position.</summary>
    Task<ProjectListResponse> ListProjectsAsync(CancellationToken ct = default);

    Task<ProjectDto> CreateProjectAsync(string name, CancellationToken ct = default);

    Task<ProjectDto> RenameProjectAsync(string id, string name, CancellationToken ct = default);

    /// <summary>Deletes a project; its conversations become unfiled server-side.</summary>
    Task DeleteProjectAsync(string id, CancellationToken ct = default);

    /// <summary>Patch a project's shared instructions (Phase 12). Same
    /// contract as the web Projects page — the server caps the length and
    /// every chat in the project inherits the text at prompt-compile time.</summary>
    Task<ProjectDto> UpdateProjectInstructionsAsync(
        string id, string instructions, CancellationToken ct = default);

    /// <summary>Attach (or detach, with null) the project's knowledge base.</summary>
    Task<ProjectDto> SetProjectKnowledgeBaseAsync(
        string id, string? knowledgeBaseId, CancellationToken ct = default);

    /// <summary>Files a conversation into a project, or unfiles it when <paramref name="projectId"/> is null.</summary>
    Task MoveConversationToProjectAsync(string conversationId, string? projectId, CancellationToken ct = default);

    /// <summary>Account-wide custom instructions (Phase 13). The same two
    /// fields the web Projects page edits — read them before showing the
    /// editor so the desktop never overwrites text set on the web.</summary>
    Task<UserPreferencesDto> GetPreferencesAsync(CancellationToken ct = default);

    /// <summary>Upserts the account-wide custom instructions. The server
    /// merges field-by-field, so both are always sent together here.</summary>
    Task<UserPreferencesDto> UpdatePreferencesAsync(
        string about, string respond, CancellationToken ct = default);

    // ── Canvas artifacts (Phase 15 — the server C3 surface) ────────────────
    // The SAME endpoints the web canvas uses, so a version written on the
    // desktop shows up on the web and vice versa.

    /// <summary>Newest version of every artifact in the conversation.</summary>
    Task<CanvasArtifactListResponse> ListCanvasArtifactsAsync(
        string conversationId, CancellationToken ct = default);

    /// <summary>Appends the NEXT version for this artifact key (v1 when new).</summary>
    Task<CanvasArtifactDto> SaveCanvasArtifactAsync(
        string conversationId, SaveCanvasArtifactRequest request, CancellationToken ct = default);

    /// <summary>Full version history for one lineage, newest first.</summary>
    Task<CanvasVersionListResponse> ListCanvasVersionsAsync(
        string conversationId, string artifactKey, CancellationToken ct = default);

    /// <summary>Reverts by APPENDING the old content as a new version — the
    /// history is never rewritten.</summary>
    Task<CanvasArtifactDto> RevertCanvasArtifactAsync(
        string conversationId, string artifactKey, int version, CancellationToken ct = default);

    // ── Media generation (Phase 14 Task 3) ─────────────────────────────────
    // Direct dispatch: submit → poll → fetch the artifact bytes. See the
    // remarks on AudioGenerationRequest for why audio is not a tool.

    Task<MediaGenSubmitResponse> SubmitAudioGenerationAsync(
        AudioGenerationRequest request, CancellationToken ct = default);

    Task<MediaJobStatus> GetMediaJobAsync(string jobId, CancellationToken ct = default);

    /// <summary>The finished artifact's bytes, through the authed client — the
    /// job's output_url is gateway-relative and cannot be fetched directly.</summary>
    Task<byte[]> DownloadMediaJobOutputAsync(string jobId, CancellationToken ct = default);

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
    /// Phase 5 Task 3: GET .../messages/{messageId}/stream — reattach to an
    /// IN-FLIGHT server-side generation. Same SSE vocabulary as the POST send;
    /// the already-generated prefix arrives as one large content_delta, then
    /// live deltas. Throws HttpRequestException with StatusCode 409
    /// (RESUME_UNAVAILABLE — not attachable yet, retry with backoff) or 404
    /// (message/conversation gone).
    /// </summary>
    IAsyncEnumerable<SseFrame> ResumeMessageStreamAsync(
        string conversationId, string messageId, CancellationToken ct = default);

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

    /// <summary>Multipart upload (form field "file"). Progress reports 0–100.
    /// <paramref name="clientId"/> is the optional dedup key (FIX 10): when
    /// set, re-uploading the same (conversation, clientId) returns the
    /// existing server row instead of duplicating it.</summary>
    Task<FileAttachmentDto> UploadFileAsync(
        string conversationId, Stream content, string fileName, string contentType, long? length,
        IProgress<double>? progress = null, string? clientId = null, CancellationToken ct = default);

    /// <summary>GET .../files/{id}/text — the text the server extracted from an
    /// attachment, plus the extraction status. Used by read_document to read a
    /// scanned PDF, which only the server-side OCR pipeline can handle. The
    /// status is authoritative: text arrives empty until it reads "completed".</summary>
    Task<FileTextDto> GetFileTextAsync(string fileId, CancellationToken ct = default);

    /// <summary>GET .../files/{id}/preview-data — the STRUCTURE of an office
    /// attachment (Phase 16): a window of one sheet with every sheet's name,
    /// Word blocks in document order, or one entry per slide.
    ///
    /// <paramref name="sheet"/> only means anything for a workbook;
    /// <paramref name="offset"/>/<paramref name="limit"/> count rows, blocks or
    /// slides depending on the file. Throws <see cref="HttpRequestException"/>
    /// with 415 when the type has no structured view and 503 when the server
    /// cannot produce one — in both cases the caller falls back to
    /// <see cref="GetFileTextAsync"/>, which is what every deployment had
    /// before this route existed.</summary>
    Task<FilePreviewDataDto> GetFilePreviewDataAsync(
        string fileId, int sheet = 0, int offset = 0, int limit = 0,
        CancellationToken ct = default);

    // ── Plan execution (R7 Feature C) ───────────────────────────────────────

    /// <summary>POST .../excel/execute — the fenced plan JSON goes under "plan".</summary>
    Task<PlanExecutionResult> ExecuteExcelPlanAsync(
        string conversationId, ExecuteExcelPlanRequest request, CancellationToken ct = default);

    /// <summary>POST .../document/execute — docx / pdf / pptx.</summary>
    Task<PlanExecutionResult> ExecuteDocumentPlanAsync(
        string conversationId, ExecuteDocumentPlanRequest request, CancellationToken ct = default);

    // ── Code interpreter (Phase 13 Part B) ──────────────────────────────────

    /// <summary>POST api/v1/code/run — runs Python in chat-edge's hardened
    /// sandbox (no network, non-root, seccomp, resource-capped) and returns
    /// stdout/stderr + any produced artifacts. <paramref name="fileIds"/> are
    /// optional caller-owned conversation files mounted read-only in the box.</summary>
    Task<CodeExecutionResult> RunPythonAsync(
        string code, IReadOnlyList<string>? fileIds = null, CancellationToken ct = default);

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
