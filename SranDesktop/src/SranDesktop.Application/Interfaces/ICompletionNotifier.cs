namespace SranDesktop.Application.Interfaces;

/// <summary>
/// OS-level "your turn finished" notification (tray balloon). Fired by the
/// chat turn's terminal path when the main window is NOT focused at completion
/// time — the in-window UI already covers the focused case. The implementation
/// remembers the last-notified conversation so a click on the notification can
/// restore the window and open that conversation.
/// </summary>
public interface ICompletionNotifier
{
    /// <param name="conversationId">The conversation the finished turn belongs to.</param>
    /// <param name="conversationTitle">Its display title (body text); may be null/empty.</param>
    /// <param name="isError">True when the turn ended in an error bubble.</param>
    void NotifyTurnCompleted(string conversationId, string? conversationTitle, bool isError);
}
