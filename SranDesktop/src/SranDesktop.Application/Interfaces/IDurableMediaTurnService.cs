using SranDesktop.Application.Events;

namespace SranDesktop.Application.Interfaces;

public interface IDurableMediaTurnService
{
    IAsyncEnumerable<ChatStreamEvent> RunAsync(
        string conversationId,
        string userContent,
        IReadOnlyList<string>? fileIds,
        bool imageMode,
        bool videoMode,
        CancellationToken ct = default);
}
