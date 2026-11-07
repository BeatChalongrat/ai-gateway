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

    /// <summary>Batch 2 T2 — the stream idle threshold, in ONE place. The
    /// server heartbeats every ~15s (chat-edge heartbeatInterval), and the
    /// reader surfaces those as heartbeat frames, so a stream that is quiet
    /// for this long (≈6 missed beats) is genuinely stalled — a long tool
    /// run stays alive via heartbeats. Mirrors web STREAM_IDLE.timeoutMs.
    /// Mutable field (not const) purely so tests can shorten it.</summary>
    internal static TimeSpan StreamIdleTimeout = TimeSpan.FromSeconds(90);

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
        // T2 — idle watchdog. Any frame (heartbeat ":" comments included —
        // the chat-edge client opts the reader into yielding them) resets
        // the clock; a wait that outlives StreamIdleTimeout raises
        // StreamStalledEvent(true) so the bubble can offer "still working —
        // retry?", and the next frame clears it. WaitAsync disposes its
        // timer per cycle, so a fast delta stream doesn't leak timers.
        var stalled = false;
        // Server-declared stall (provider_stall). Held separately because
        // the byte clock below CANNOT clear it: chat-edge keeps sending
        // heartbeats while the provider is silent, so the very next frame
        // would wipe a stall the server can see and we cannot.
        var serverStalled = false;
        var e = frames.GetAsyncEnumerator();
        try
        {
            while (true)
            {
                var moveTask = e.MoveNextAsync().AsTask();
                var has = false;
                while (true)
                {
                    var timedOut = false;
                    try
                    {
                        has = await moveTask.WaitAsync(StreamIdleTimeout);
                    }
                    catch (TimeoutException)
                    {
                        timedOut = true; // yield is illegal inside a catch
                    }
                    if (!timedOut)
                        break;
                    if (!stalled)
                    {
                        stalled = true;
                        yield return new StreamStalledEvent(true);
                    }
                }
                if (!has)
                    yield break;

                var frame = e.Current;

                // chat-edge's verdict wins over the byte clock in both
                // directions — it is the layer holding the connection.
                if (frame.Event == "provider_stall")
                {
                    var p = Parse<ProviderStallPayload>(frame);
                    if (p is not null)
                    {
                        serverStalled = p.Stalled;
                        if (stalled != p.Stalled)
                        {
                            stalled = p.Stalled;
                            yield return new StreamStalledEvent(p.Stalled);
                        }
                    }
                    continue;
                }

                if (stalled && !serverStalled)
                {
                    stalled = false;
                    yield return new StreamStalledEvent(false);
                }
                // Heartbeat sentinel — liveness only, nothing to map.
                if (frame.Event == ":")
                    continue;

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
        finally
        {
            await e.DisposeAsync();
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

        "reasoning_notice" when Parse<ReasoningNoticePayload>(frame) is { } p =>
            new ReasoningNoticeEvent(p.Model, p.Reason),

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
