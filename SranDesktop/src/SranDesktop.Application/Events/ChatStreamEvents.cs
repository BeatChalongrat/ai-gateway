using SranDesktop.Domain.ValueObjects;

namespace SranDesktop.Application.Events;

/// <summary>Typed events emitted by IChatService while streaming an assistant turn.</summary>
public abstract record ChatStreamEvent;

public sealed record StreamStartedEvent(string MessageId) : ChatStreamEvent;

public sealed record ContentDeltaEvent(string Text) : ChatStreamEvent;

public sealed record ReasoningDeltaEvent(string Text) : ChatStreamEvent;

/// <summary>Phase markers: analysis_status / search_status / image_generating / video_generating.</summary>
public sealed record StreamStatusEvent(string Kind, string? Status) : ChatStreamEvent;

public sealed record FallbackInfoEvent(string? RequestedModel, string? ActualModel, string? Reason) : ChatStreamEvent;

/// <summary>The reply was produced without the requested reasoning (the
/// resolved model can't reason). Informational — never an error.</summary>
public sealed record ReasoningNoticeEvent(string? Model, string? Reason) : ChatStreamEvent;

public sealed record StreamCompletedEvent(TokenUsage? Usage) : ChatStreamEvent;

public sealed record StreamErrorEvent(string? Code, string Message) : ChatStreamEvent;

/// <summary>Generated/attached files announced by the server (message_files).</summary>
public sealed record MessageFilesEvent(IReadOnlyList<MessageFileInfo> Files) : ChatStreamEvent;

public sealed record MessageFileInfo(
    string Id,
    string Filename,
    string ContentType,
    long SizeBytes,
    string? Url);

/// <summary>Server-side memory activity during a turn (memory_saved / memory_suggested).</summary>
public sealed record MemoryActivityEvent(string Kind) : ChatStreamEvent;
