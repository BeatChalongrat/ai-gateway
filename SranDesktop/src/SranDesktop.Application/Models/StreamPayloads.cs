using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

// Wire shapes of chat-edge SSE event payloads (see apps/web-chat/src/lib/types.ts).

public sealed record MessageStartPayload(
    [property: JsonPropertyName("message_id")] string MessageId,
    [property: JsonPropertyName("conversation_id")] string? ConversationId);

/// <summary>content_delta / reasoning_delta both carry a "delta" text fragment.</summary>
public sealed record DeltaPayload(
    [property: JsonPropertyName("delta")] string Delta);

public sealed record StatusPayload(
    [property: JsonPropertyName("status")] string? Status);

/// <summary>Wire token usage is {input, output} (+optional reasoning).</summary>
public sealed record TokenUsagePayload(
    [property: JsonPropertyName("input")] int Input,
    [property: JsonPropertyName("output")] int Output,
    [property: JsonPropertyName("reasoning")] int? Reasoning);

public sealed record MessageCompletePayload(
    [property: JsonPropertyName("message_id")] string? MessageId,
    [property: JsonPropertyName("token_usage")] TokenUsagePayload? TokenUsage);

public sealed record StreamErrorPayload(
    [property: JsonPropertyName("code")] string? Code,
    [property: JsonPropertyName("message")] string? Message);

/// <summary>message_files: {"message_id":"...","files":[fileResponse,...]}</summary>
public sealed record MessageFilesPayload(
    [property: JsonPropertyName("message_id")] string? MessageId,
    [property: JsonPropertyName("files")] List<MessageFilePayload> Files);

public sealed record MessageFilePayload(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("content_type")] string? ContentType,
    [property: JsonPropertyName("file_size_bytes")] long FileSizeBytes,
    [property: JsonPropertyName("download_url")] string? DownloadUrl);

public sealed record FallbackInfoPayload(
    [property: JsonPropertyName("requested_model")] string? RequestedModel,
    [property: JsonPropertyName("actual_model")] string? ActualModel,
    [property: JsonPropertyName("reason")] string? Reason);

/// <summary>reasoning_notice — the gateway answered without the requested
/// reasoning because the resolved model can't reason. Informational.</summary>
public sealed record ReasoningNoticePayload(
    [property: JsonPropertyName("model")] string? Model,
    [property: JsonPropertyName("reason")] string? Reason);

/// <summary>provider_stall — chat-edge holds the upstream connection and
/// reports that the MODEL has produced nothing for its threshold
/// (Stalled=true), or that output resumed (Stalled=false). Distinct from
/// the client's own idle watchdog, which only sees whether BYTES arrive:
/// heartbeats keep flowing while a provider is stalled, so the client
/// cannot detect this case itself.</summary>
public sealed record ProviderStallPayload(
    [property: JsonPropertyName("stalled")] bool Stalled,
    [property: JsonPropertyName("waiting_seconds")] int WaitingSeconds = 0);
