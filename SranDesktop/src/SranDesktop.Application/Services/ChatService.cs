using System.Text.Json;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.ValueObjects;

namespace SranDesktop.Application.Services;

public sealed class ChatService : IChatService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly IChatEdgeApiClient _api;
    private readonly ILogger<ChatService> _logger;

    public ChatService(IChatEdgeApiClient api, ILogger<ChatService> logger)
    {
        _api = api;
        _logger = logger;
    }

    public IAsyncEnumerable<ChatStreamEvent> SendMessageAsync(
        string conversationId,
        string content,
        IReadOnlyList<string>? fileIds = null,
        bool imageMode = false,
        bool videoMode = false,
        CancellationToken ct = default)
        => MapFramesAsync(_api.StreamMessageAsync(conversationId, content, fileIds, imageMode, videoMode, ct));

    public IAsyncEnumerable<ChatStreamEvent> ResumeMessageAsync(
        string conversationId,
        string messageId,
        CancellationToken ct = default)
        // Phase 5 Task 3: the reattach stream speaks the same SSE vocabulary
        // as the send stream — one mapper serves both.
        => MapFramesAsync(_api.ResumeMessageStreamAsync(conversationId, messageId, ct));

    private async IAsyncEnumerable<ChatStreamEvent> MapFramesAsync(IAsyncEnumerable<SseFrame> frames)
    {
        await foreach (var frame in frames)
        {
            ChatStreamEvent? mapped;
            try
            {
                mapped = MapFrame(frame);
            }
            catch (JsonException ex)
            {
                _logger.LogWarning(ex, "Skipping malformed SSE frame (event={Event})", frame.Event);
                continue;
            }

            if (mapped is not null)
                yield return mapped;
        }
    }

    private ChatStreamEvent? MapFrame(SseFrame frame) => frame.Event switch
    {
        "message_start" when Parse<MessageStartPayload>(frame) is { } p =>
            new StreamStartedEvent(p.MessageId),

        "content_delta" when Parse<DeltaPayload>(frame) is { } p =>
            new ContentDeltaEvent(p.Delta),

        "reasoning_delta" when Parse<DeltaPayload>(frame) is { } p =>
            new ReasoningDeltaEvent(p.Delta),

        "analysis_status" => new StreamStatusEvent("analysis", Parse<StatusPayload>(frame)?.Status),
        "search_status" => new StreamStatusEvent("search", Parse<StatusPayload>(frame)?.Status),
        "image_generating" => new StreamStatusEvent("image", Parse<StatusPayload>(frame)?.Status),
        "video_generating" => new StreamStatusEvent("video", Parse<StatusPayload>(frame)?.Status),

        "fallback_info" when Parse<FallbackInfoPayload>(frame) is { } p =>
            new FallbackInfoEvent(p.RequestedModel, p.ActualModel, p.Reason),

        "message_complete" => new StreamCompletedEvent(
            ToTokenUsage(Parse<MessageCompletePayload>(frame)?.TokenUsage)),

        "error" when Parse<StreamErrorPayload>(frame) is { } p =>
            new StreamErrorEvent(p.Code, p.Message ?? "The server reported an error."),

        "message_files" when Parse<MessageFilesPayload>(frame) is { } p =>
            new MessageFilesEvent(p.Files.Select(f => new MessageFileInfo(
                f.Id, f.Filename, f.ContentType ?? "application/octet-stream",
                f.FileSizeBytes, f.DownloadUrl)).ToList()),

        "memory_saved" or "memory_suggested" => new MemoryActivityEvent(frame.Event!),

        // message_sources etc. are ignored for now.
        _ => null
    };

    private static T? Parse<T>(SseFrame frame) where T : class =>
        JsonSerializer.Deserialize<T>(frame.Data, JsonOptions);

    private static TokenUsage? ToTokenUsage(TokenUsagePayload? payload) =>
        payload is null ? null : new TokenUsage(payload.Input, payload.Output, payload.Input + payload.Output);
}
