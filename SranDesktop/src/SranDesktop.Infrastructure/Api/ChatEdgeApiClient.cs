using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text.Json;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;

namespace SranDesktop.Infrastructure.Api;

/// <summary>
/// Typed client for chat-edge-api. BaseAddress is configured in DI
/// (e.g. http://localhost:4000); all paths here are api/v1/-relative.
/// The Bearer token and 401-refresh-retry are handled by AuthTokenHandler.
/// </summary>
public sealed class ChatEdgeApiClient : IChatEdgeApiClient
{
    internal static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;

    public ChatEdgeApiClient(HttpClient http)
    {
        _http = http;
    }

    public async Task<ConversationListResponse> ListConversationsAsync(
        string? cursor = null, int limit = 50, CancellationToken ct = default)
    {
        var url = $"api/v1/conversations?limit={limit}";
        if (!string.IsNullOrEmpty(cursor))
            url += $"&cursor={Uri.EscapeDataString(cursor)}";
        return await GetAsync<ConversationListResponse>(url, ct);
    }

    public async Task<ConversationListResponse> SearchConversationsAsync(
        string query, int limit = 50, CancellationToken ct = default)
    {
        var url = $"api/v1/conversations?q={Uri.EscapeDataString(query)}&limit={limit}";
        return await GetAsync<ConversationListResponse>(url, ct);
    }

    public async Task<ConversationDto> CreateConversationAsync(string modelId, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync(
            "api/v1/conversations", new CreateConversationRequest(modelId), JsonOptions, deadline.Token);
        return await ReadAsync<ConversationDto>(response, deadline.Token);
    }

    public async Task<ConversationDto> GetConversationAsync(string id, CancellationToken ct = default)
    {
        return await GetAsync<ConversationDto>(
            $"api/v1/conversations/{Uri.EscapeDataString(id)}", ct);
    }

    public async Task<ConversationDto> RenameConversationAsync(string id, string title, CancellationToken ct = default)
    {
        return await PatchAsync(id, new Dictionary<string, object?> { ["title"] = title }, ct);
    }

    public async Task<ConversationDto> SetConversationPinnedAsync(string id, bool pinned, CancellationToken ct = default)
    {
        return await PatchAsync(id, new Dictionary<string, object?> { ["is_pinned"] = pinned }, ct);
    }

    public async Task<ConversationDto> SetConversationModelAsync(string id, string modelId, CancellationToken ct = default)
    {
        return await PatchAsync(id, new Dictionary<string, object?> { ["model_id"] = modelId }, ct);
    }

    public async Task<ConversationDto> SetConversationKnowledgeBaseAsync(
        string id, string? knowledgeBaseId, CancellationToken ct = default)
    {
        // null unbinds; the server accepts a JSON null for knowledge_base_id.
        return await PatchAsync(id, new Dictionary<string, object?> { ["knowledge_base_id"] = knowledgeBaseId }, ct);
    }

    public async Task<ConversationDto> SetConversationMemoryAsync(string id, bool enabled, CancellationToken ct = default)
    {
        return await PatchAsync(id, new Dictionary<string, object?> { ["memory_enabled"] = enabled }, ct);
    }

    public async Task DeleteConversationAsync(string id, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.DeleteAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(id)}", deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    public async Task<ImportConversationResponse> ImportConversationAsync(
        string clientId, string? title, string? modelId, DateTimeOffset createdAt,
        CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync(
            "api/v1/conversations/import",
            new ImportConversationRequest(clientId, title, modelId,
                createdAt.UtcDateTime.ToString("o")),
            JsonOptions, deadline.Token);
        return await ReadAsync<ImportConversationResponse>(response, deadline.Token);
    }

    public async Task<ImportMessagesResponse> ImportMessagesAsync(
        string serverConversationId, IReadOnlyList<ImportMessageItem> messages,
        CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(serverConversationId)}/messages/import",
            new ImportMessagesRequest(messages.ToList()), JsonOptions, deadline.Token);
        return await ReadAsync<ImportMessagesResponse>(response, deadline.Token);
    }

    public async Task<FolderListResponse> ListFoldersAsync(CancellationToken ct = default)
    {
        return await GetAsync<FolderListResponse>("api/v1/folders", ct);
    }

    public async Task<FolderDto> CreateFolderAsync(string name, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync(
            "api/v1/folders", new Dictionary<string, string> { ["name"] = name }, JsonOptions, deadline.Token);
        return await ReadAsync<FolderDto>(response, deadline.Token);
    }

    public async Task<FolderDto> RenameFolderAsync(string id, string name, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PutAsJsonAsync(
            $"api/v1/folders/{Uri.EscapeDataString(id)}",
            new Dictionary<string, string> { ["name"] = name }, JsonOptions, deadline.Token);
        return await ReadAsync<FolderDto>(response, deadline.Token);
    }

    public async Task DeleteFolderAsync(string id, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.DeleteAsync(
            $"api/v1/folders/{Uri.EscapeDataString(id)}", deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    public async Task MoveConversationToFolderAsync(
        string conversationId, string? folderId, CancellationToken ct = default)
    {
        // folder_id: "<uuid>" files; null unfiles (the server reads a JSON null).
        using var deadline = Deadline(ct);
        using var response = await _http.PutAsJsonAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(conversationId)}/folder",
            new Dictionary<string, string?> { ["folder_id"] = folderId }, JsonOptions, deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    public async Task<ModelListResponse> ListModelsAsync(CancellationToken ct = default)
    {
        return await GetAsync<ModelListResponse>("api/v1/models", ct);
    }

    public async Task<UsageResponse> GetUsageAsync(CancellationToken ct = default)
    {
        return await GetAsync<UsageResponse>("api/v1/usage/me", ct);
    }

    public async Task<KnowledgeBaseListResponse> ListKnowledgeBasesAsync(CancellationToken ct = default)
    {
        return await GetAsync<KnowledgeBaseListResponse>("api/v1/knowledge-bases", ct);
    }

    public async Task<string> ExportConversationAsync(string id, string format, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.GetAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(id)}/export?format={Uri.EscapeDataString(format)}",
            deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
        return await response.Content.ReadAsStringAsync(deadline.Token);
    }

    public async Task<MessageListResponse> ListMessagesAsync(
        string conversationId, int limit = 50, string? before = null, CancellationToken ct = default)
    {
        var url = $"api/v1/conversations/{Uri.EscapeDataString(conversationId)}/messages?limit={limit}";
        if (!string.IsNullOrEmpty(before))
            url += $"&before={Uri.EscapeDataString(before)}";
        return await GetAsync<MessageListResponse>(url, ct);
    }

    public async IAsyncEnumerable<SseFrame> StreamMessageAsync(
        string conversationId, string content, IReadOnlyList<string>? fileIds = null,
        bool imageMode = false, bool videoMode = false,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var body = new Dictionary<string, object?>
        {
            ["content"] = content,
            ["stream"] = true
        };
        if (fileIds is { Count: > 0 })
            body["file_ids"] = fileIds;
        if (imageMode)
            body["image_mode"] = true;
        if (videoMode)
            body["video_mode"] = true; // forward-compat; current server reads image_mode only

        using var request = new HttpRequestMessage(
            HttpMethod.Post,
            $"api/v1/conversations/{Uri.EscapeDataString(conversationId)}/messages")
        {
            Content = JsonContent.Create(body, options: JsonOptions)
        };
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));

        using var response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
        await EnsureSuccessAsync(response, ct);

        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        await foreach (var frame in SseStreamReader.ParseAsync(stream, ct))
            yield return frame;
    }

    public async Task<bool> CancelStreamAsync(string conversationId, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(conversationId)}/cancel",
            content: null, deadline.Token);
        var result = await ReadAsync<CancelStreamResponse>(response, deadline.Token);
        return result.Cancelled;
    }

    public async Task SendFeedbackAsync(string messageId, string rating, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync(
            $"api/v1/messages/{Uri.EscapeDataString(messageId)}/feedback",
            new Dictionary<string, string> { ["rating"] = rating }, JsonOptions, deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    public async Task<ContextInfoDto> GetContextInfoAsync(
        string conversationId, CancellationToken ct = default)
    {
        // Deliberately NOT the retrying GetAsync: this is a best-effort
        // after-turn probe — one failed attempt simply means "no nudge".
        using var deadline = Deadline(ct);
        using var response = await _http.GetAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(conversationId)}/context-info",
            deadline.Token);
        return await ReadAsync<ContextInfoDto>(response, deadline.Token);
    }

    public async Task<MessageSearchResponse> SearchMessagesAsync(
        string query, int limit = 20, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.GetAsync(
            $"api/v1/search?q={Uri.EscapeDataString(query)}&scope=messages&limit={limit}",
            deadline.Token);
        return await ReadAsync<MessageSearchResponse>(response, deadline.Token);
    }

    public async Task<GenerateTitleResponse> GenerateTitleAsync(
        string conversationId, CancellationToken ct = default)
    {
        // Title generation calls an LLM server-side — give it a longer deadline.
        using var deadline = Deadline(ct, TimeSpan.FromSeconds(60));
        using var response = await _http.PostAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(conversationId)}/generate-title",
            content: null, deadline.Token);
        return await ReadAsync<GenerateTitleResponse>(response, deadline.Token);
    }

    public async Task<FileAttachmentDto> UploadFileAsync(
        string conversationId, Stream content, string fileName, string contentType, long? length,
        IProgress<double>? progress = null, string? clientId = null, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct, TimeSpan.FromMinutes(10));

        using var form = new MultipartFormDataContent();
        // Dedup key first (FIX 10) — a plain text field the server reads via
        // FormValue; re-uploads of the same (conversation, client_id) return
        // the existing row instead of a duplicate.
        if (!string.IsNullOrEmpty(clientId))
            form.Add(new StringContent(clientId), "client_id");
        var streamContent = new StreamContent(
            new ProgressReportingStream(content, length, progress), 81920);
        streamContent.Headers.ContentType = new MediaTypeHeaderValue(contentType);
        form.Add(streamContent, "file", fileName);

        using var response = await _http.PostAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(conversationId)}/files",
            form, deadline.Token);
        var result = await ReadAsync<FileAttachmentDto>(response, deadline.Token);
        progress?.Report(100);
        return result;
    }

    public async Task<byte[]> DownloadFileAsync(string downloadUrl, CancellationToken ct = default)
    {
        // download_url arrives relative ("/api/v1/files/{id}/download" or
        // "/files/{id}/download"); normalize to an api/v1-relative path. The
        // endpoint may 307-redirect to presigned storage — the handler follows it.
        var path = downloadUrl.TrimStart('/');
        if (!path.StartsWith("api/", StringComparison.OrdinalIgnoreCase))
            path = "api/v1/" + path;

        using var deadline = Deadline(ct, TimeSpan.FromMinutes(10));
        using var response = await _http.GetAsync(path, deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
        return await response.Content.ReadAsByteArrayAsync(deadline.Token);
    }

    // ── Plan execution (R7 Feature C) ───────────────────────────────────────

    public async Task<PlanExecutionResult> ExecuteExcelPlanAsync(
        string conversationId, ExecuteExcelPlanRequest request, CancellationToken ct = default)
    {
        return await ExecutePlanAsync("excel", conversationId, request, ct);
    }

    public async Task<PlanExecutionResult> ExecuteDocumentPlanAsync(
        string conversationId, ExecuteDocumentPlanRequest request, CancellationToken ct = default)
    {
        return await ExecutePlanAsync("document", conversationId, request, ct);
    }

    private async Task<PlanExecutionResult> ExecutePlanAsync<TRequest>(
        string kind, string conversationId, TRequest request, CancellationToken ct)
    {
        // Plan execution runs openpyxl/python-docx server-side — generous deadline.
        using var deadline = Deadline(ct, TimeSpan.FromMinutes(5));
        using var response = await _http.PostAsJsonAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(conversationId)}/{kind}/execute",
            request, JsonOptions, deadline.Token);
        return await ReadAsync<PlanExecutionResult>(response, deadline.Token);
    }

    // ── Code interpreter (Phase 13 Part B) ──────────────────────────────────

    public async Task<CodeExecutionResult> RunPythonAsync(
        string code, IReadOnlyList<string>? fileIds = null, CancellationToken ct = default)
    {
        // A sandbox run is bounded server-side (60s wall); allow generous
        // client headroom for container start + teardown.
        using var deadline = Deadline(ct, TimeSpan.FromMinutes(2));
        using var response = await _http.PostAsJsonAsync(
            "api/v1/code/run",
            new Dictionary<string, object?>
            {
                ["code"] = code,
                ["files"] = fileIds ?? new List<string>(),
            },
            JsonOptions, deadline.Token);
        return await ReadAsync<CodeExecutionResult>(response, deadline.Token);
    }

    // ── Knowledge base management (R7 Feature D) ────────────────────────────

    public async Task<KnowledgeBaseDetailListResponse> ListKnowledgeBasesDetailedAsync(CancellationToken ct = default)
    {
        return await GetAsync<KnowledgeBaseDetailListResponse>("api/v1/knowledge-bases", ct);
    }

    public async Task<KnowledgeBaseDetailDto> CreateKnowledgeBaseAsync(
        string name, string? description, int chunkSize = 512, int chunkOverlap = 50,
        CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync("api/v1/knowledge-bases",
            new Dictionary<string, object?>
            {
                ["name"] = name,
                ["description"] = description ?? "",
                ["chunk_size"] = chunkSize,
                ["chunk_overlap"] = chunkOverlap
            }, JsonOptions, deadline.Token);
        return await ReadAsync<KnowledgeBaseDetailDto>(response, deadline.Token);
    }

    public async Task<KnowledgeBaseDetailResponse> GetKnowledgeBaseAsync(string id, CancellationToken ct = default)
    {
        return await GetAsync<KnowledgeBaseDetailResponse>(
            $"api/v1/knowledge-bases/{Uri.EscapeDataString(id)}", ct);
    }

    public async Task<KnowledgeBaseDetailDto> UpdateKnowledgeBaseAsync(
        string id, string? name = null, string? description = null, bool? isActive = null,
        CancellationToken ct = default)
    {
        var patch = new Dictionary<string, object?>();
        if (name is not null) patch["name"] = name;
        if (description is not null) patch["description"] = description;
        if (isActive is not null) patch["is_active"] = isActive;

        using var deadline = Deadline(ct);
        using var request = new HttpRequestMessage(
            HttpMethod.Patch, $"api/v1/knowledge-bases/{Uri.EscapeDataString(id)}")
        {
            Content = JsonContent.Create(patch, options: JsonOptions)
        };
        using var response = await _http.SendAsync(request, deadline.Token);
        return await ReadAsync<KnowledgeBaseDetailDto>(response, deadline.Token);
    }

    public async Task DeleteKnowledgeBaseAsync(string id, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.DeleteAsync(
            $"api/v1/knowledge-bases/{Uri.EscapeDataString(id)}", deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    public async Task<KbDocumentDto> UploadKbDocumentAsync(
        string kbId, Stream content, string fileName, string contentType, long? length,
        IProgress<double>? progress = null, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct, TimeSpan.FromMinutes(10));

        using var form = new MultipartFormDataContent();
        var streamContent = new StreamContent(
            new ProgressReportingStream(content, length, progress), 81920);
        streamContent.Headers.ContentType = new MediaTypeHeaderValue(contentType);
        form.Add(streamContent, "file", fileName);

        using var response = await _http.PostAsync(
            $"api/v1/knowledge-bases/{Uri.EscapeDataString(kbId)}/documents",
            form, deadline.Token);
        var result = await ReadAsync<KbDocumentDto>(response, deadline.Token);
        progress?.Report(100);
        return result;
    }

    public async Task DeleteKbDocumentAsync(string kbId, string docId, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.DeleteAsync(
            $"api/v1/knowledge-bases/{Uri.EscapeDataString(kbId)}/documents/{Uri.EscapeDataString(docId)}",
            deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    public async Task<KbDocumentDto> ReindexKbDocumentAsync(string kbId, string docId, CancellationToken ct = default)
    {
        // Reindex chunks + embeds server-side — allow more than the default 30s.
        using var deadline = Deadline(ct, TimeSpan.FromMinutes(5));
        using var response = await _http.PostAsync(
            $"api/v1/knowledge-bases/{Uri.EscapeDataString(kbId)}/documents/{Uri.EscapeDataString(docId)}/reindex",
            content: null, deadline.Token);
        return await ReadAsync<KbDocumentDto>(response, deadline.Token);
    }

    // ── Memory management (R7 Feature E) ────────────────────────────────────

    public async Task<MemoryListResponse> ListMemoriesAsync(
        string? scopeType = null, string? memoryType = null, string? status = null,
        int limit = 100, int offset = 0, CancellationToken ct = default)
    {
        var url = $"api/v1/memories?limit={limit}&offset={offset}";
        if (!string.IsNullOrEmpty(scopeType)) url += $"&scope_type={Uri.EscapeDataString(scopeType)}";
        if (!string.IsNullOrEmpty(memoryType)) url += $"&memory_type={Uri.EscapeDataString(memoryType)}";
        if (!string.IsNullOrEmpty(status)) url += $"&status={Uri.EscapeDataString(status)}";
        return await GetAsync<MemoryListResponse>(url, ct);
    }

    public async Task<MemoryDto> CreateMemoryAsync(
        string content, string memoryType, string? category = null, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync("api/v1/memories",
            new Dictionary<string, object?>
            {
                ["scope_type"] = "global_user",
                ["memory_type"] = memoryType,
                ["category"] = category,
                ["content"] = content
            }, JsonOptions, deadline.Token);
        return await ReadAsync<MemoryDto>(response, deadline.Token);
    }

    public async Task<MemoryDto> UpdateMemoryAsync(
        string id, string? content = null, string? memoryType = null, string? category = null,
        CancellationToken ct = default)
    {
        var patch = new Dictionary<string, object?>();
        if (content is not null) patch["content"] = content;
        if (memoryType is not null) patch["memory_type"] = memoryType;
        if (category is not null) patch["category"] = category;

        using var deadline = Deadline(ct);
        using var response = await _http.PutAsJsonAsync(
            $"api/v1/memories/{Uri.EscapeDataString(id)}", patch, JsonOptions, deadline.Token);
        return await ReadAsync<MemoryDto>(response, deadline.Token);
    }

    public async Task DeleteMemoryAsync(string id, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.DeleteAsync(
            $"api/v1/memories/{Uri.EscapeDataString(id)}", deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    public async Task<MemoryDto> PinMemoryAsync(string id, bool pinned, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync(
            $"api/v1/memories/{Uri.EscapeDataString(id)}/pin",
            new Dictionary<string, bool> { ["pinned"] = pinned }, JsonOptions, deadline.Token);
        return await ReadAsync<MemoryDto>(response, deadline.Token);
    }

    public async Task<MemoryDto> ConfirmMemoryAsync(string id, bool confirmed, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsJsonAsync(
            $"api/v1/memories/{Uri.EscapeDataString(id)}/confirm",
            new Dictionary<string, bool> { ["confirmed"] = confirmed }, JsonOptions, deadline.Token);
        return await ReadAsync<MemoryDto>(response, deadline.Token);
    }

    public async Task<BulkDeleteResponse> ClearAllMemoriesAsync(CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.DeleteAsync("api/v1/memories/all", deadline.Token);
        return await ReadAsync<BulkDeleteResponse>(response, deadline.Token);
    }

    public async Task<int> GetPendingMemoryCountAsync(CancellationToken ct = default)
    {
        var response = await GetAsync<PendingCountResponse>("api/v1/memories/pending-count", ct);
        return response.Count;
    }

    public async Task<string> ExportMemoriesAsync(CancellationToken ct = default)
    {
        // Two-step: prepare the export, then download its JSON.
        using var deadline = Deadline(ct, TimeSpan.FromMinutes(2));
        using var prepare = await _http.PostAsync(
            "api/v1/account/export-memories", content: null, deadline.Token);
        var export = await ReadAsync<MemoryExportResponse>(prepare, deadline.Token);

        using var download = await _http.GetAsync(
            $"api/v1/account/exports/{Uri.EscapeDataString(export.ExportId)}/download",
            deadline.Token);
        await EnsureSuccessAsync(download, deadline.Token);
        return await download.Content.ReadAsStringAsync(deadline.Token);
    }

    public async Task PurgeMemoriesAsync(CancellationToken ct = default)
    {
        // The server refuses the purge without this exact phrase (account.go).
        using var deadline = Deadline(ct, TimeSpan.FromMinutes(2));
        using var request = new HttpRequestMessage(HttpMethod.Delete, "api/v1/account/purge-memories")
        {
            Content = JsonContent.Create(
                new Dictionary<string, string> { ["confirm"] = "DELETE ALL MY MEMORIES" },
                options: JsonOptions)
        };
        using var response = await _http.SendAsync(request, deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    // ── Sharing (R9 Feature K) ──────────────────────────────────────────────

    public async Task<ShareResponse> ShareConversationAsync(string id, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.PostAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(id)}/share", content: null, deadline.Token);
        return await ReadAsync<ShareResponse>(response, deadline.Token);
    }

    public async Task RevokeShareAsync(string id, CancellationToken ct = default)
    {
        using var deadline = Deadline(ct);
        using var response = await _http.DeleteAsync(
            $"api/v1/conversations/{Uri.EscapeDataString(id)}/share", deadline.Token);
        await EnsureSuccessAsync(response, deadline.Token);
    }

    /// <summary>Read-through wrapper that reports upload percentage as HttpClient drains the stream.</summary>
    private sealed class ProgressReportingStream : Stream
    {
        private readonly Stream _inner;
        private readonly long? _length;
        private readonly IProgress<double>? _progress;
        private long _read;

        public ProgressReportingStream(Stream inner, long? length, IProgress<double>? progress)
        {
            _inner = inner;
            _length = length;
            _progress = progress;
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            var n = _inner.Read(buffer, offset, count);
            Report(n);
            return n;
        }

        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct = default)
        {
            var n = await _inner.ReadAsync(buffer, ct);
            Report(n);
            return n;
        }

        private void Report(int bytesRead)
        {
            if (_progress is null || _length is not > 0) return;
            _read += bytesRead;
            _progress.Report(Math.Min(99, _read * 100.0 / _length.Value));
        }

        public override bool CanRead => _inner.CanRead;
        public override bool CanSeek => _inner.CanSeek;
        public override bool CanWrite => false;
        public override long Length => _inner.Length;
        public override long Position { get => _inner.Position; set => _inner.Position = value; }
        public override void Flush() => _inner.Flush();
        public override long Seek(long offset, SeekOrigin origin) => _inner.Seek(offset, origin);
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    // ---------- helpers ----------

    /// <summary>
    /// The shared HttpClient has an infinite timeout (SSE streams must not be
    /// cut off), so every non-streaming call gets a per-call deadline instead.
    /// </summary>
    private static CancellationTokenSource Deadline(CancellationToken ct, TimeSpan? timeout = null)
    {
        var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(timeout ?? TimeSpan.FromSeconds(30));
        return cts;
    }

    private async Task<ConversationDto> PatchAsync(
        string id, Dictionary<string, object?> patch, CancellationToken ct)
    {
        using var deadline = Deadline(ct);
        using var request = new HttpRequestMessage(
            HttpMethod.Patch, $"api/v1/conversations/{Uri.EscapeDataString(id)}")
        {
            Content = JsonContent.Create(patch, options: JsonOptions)
        };
        using var response = await _http.SendAsync(request, deadline.Token);
        return await ReadAsync<ConversationDto>(response, deadline.Token);
    }

    private Task<T> GetAsync<T>(string url, CancellationToken ct)
    {
        // Reads are idempotent — retry transient failures with backoff.
        return RetryHelper.WithRetryAsync(async () =>
        {
            using var deadline = Deadline(ct);
            using var response = await _http.GetAsync(url, deadline.Token);
            return await ReadAsync<T>(response, deadline.Token);
        }, ct: ct);
    }

    private static async Task<T> ReadAsync<T>(HttpResponseMessage response, CancellationToken ct)
    {
        await EnsureSuccessAsync(response, ct);
        return await response.Content.ReadFromJsonAsync<T>(JsonOptions, ct)
               ?? throw new HttpRequestException("Response body was empty");
    }

    private static async Task EnsureSuccessAsync(HttpResponseMessage response, CancellationToken ct)
    {
        if (response.IsSuccessStatusCode)
            return;

        var body = await response.Content.ReadAsStringAsync(ct);
        throw new HttpRequestException(
            ExtractErrorMessage(body) is { } message
                ? $"{message} (HTTP {(int)response.StatusCode})"
                : $"chat-edge request failed ({(int)response.StatusCode}): {Truncate(body, 500)}",
            null,
            response.StatusCode);
    }

    /// <summary>
    /// Error envelopes can nest provider errors as escaped JSON inside the
    /// message ("… body={\"error\":{\"message\":\"No API key …\"}}"). The last
    /// "message" occurrence is the innermost, most human-readable one.
    /// </summary>
    private static string? ExtractErrorMessage(string body)
    {
        var matches = System.Text.RegularExpressions.Regex.Matches(
            body, "\"message\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");
        if (matches.Count == 0)
            return null;

        var raw = matches[^1].Groups[1].Value;
        try
        {
            return JsonSerializer.Deserialize<string>($"\"{raw}\"");
        }
        catch (JsonException)
        {
            return raw;
        }
    }

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max] + "...";
}
