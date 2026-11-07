using SranDesktop.Application.Events;

namespace SranDesktop.Application.Interfaces;

public interface IChatService
{
    /// <summary>
    /// Sends a user message to a server conversation and streams back typed
    /// events (deltas, status markers, completion, errors) until the turn ends.
    /// </summary>
    IAsyncEnumerable<ChatStreamEvent> SendMessageAsync(
        string conversationId,
        string content,
        IReadOnlyList<string>? fileIds = null,
        bool imageMode = false,
        bool videoMode = false,
        CancellationToken ct = default);

    /// <summary>
    /// Phase 5 Task 3: reattaches to an IN-FLIGHT server-side generation and
    /// streams the same typed events <see cref="SendMessageAsync"/> yields —
    /// the already-generated prefix arrives as one large ContentDeltaEvent,
    /// then live deltas until the turn ends. Throws HttpRequestException with
    /// StatusCode 409 while the stream isn't attachable yet (retryable) and
    /// 404 when the message/conversation is gone.
    /// </summary>
    IAsyncEnumerable<ChatStreamEvent> ResumeMessageAsync(
        string conversationId,
        string messageId,
        CancellationToken ct = default);
}
