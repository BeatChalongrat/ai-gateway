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
}
