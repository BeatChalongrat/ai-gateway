using SranDesktop.Application.Models;

namespace SranDesktop.Application.Services;

/// <summary>
/// Pure decision rule for the "chat is getting long" nudge (chat-edge
/// conversations only — agent chats have their own Phase 2 context notices).
/// Nudge when the server's context compiler is already compacting history or
/// would drop messages from the next request.
/// </summary>
public static class ContextNudge
{
    public static bool ShouldNudge(ContextInfoDto info) =>
        info.Compacted || info.MessagesWouldSend < info.MessagesTotal;
}
