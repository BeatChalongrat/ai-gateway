using System.Windows;
using System.Windows.Media;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Services;

/// <summary>
/// Bridges the "Open in Canvas" button on a rendered code block to the chat
/// that owns it (Phase 15 Task 1).
///
/// The markdown renderer is a SHARED, context-free instance
/// (MarkdownViewer holds one static <c>MarkdownRenderer</c>), so a button it
/// builds cannot capture the conversation or message it belongs to. The visual
/// tree can: the message bubble's DataContext is the MessageViewModel, and the
/// chat view's is the ChatViewModel — which owns the API client and the
/// conversation id. Walking up for them is the same trick the existing
/// tool-card Retry button uses from XAML, just expressed in code.
/// </summary>
public static class CanvasLauncher
{
    public static void Open(FrameworkElement? source, string language, string code)
    {
        if (source is null)
            return;

        var messageId = FindDataContext<MessageViewModel>(source)?.Id;
        var chat = FindDataContext<ChatViewModel>(source);
        // No chat in the tree: still previewable, just not persistable — the
        // view model degrades on its own (CanPersist == false).
        chat?.OpenCanvas(language, code, messageId);
    }

    /// <summary>Nearest ancestor whose DataContext is a <typeparamref name="T"/>.</summary>
    private static T? FindDataContext<T>(DependencyObject start) where T : class
    {
        var current = start;
        while (current is not null)
        {
            if (current is FrameworkElement { DataContext: T match })
                return match;
            current = VisualTreeHelper.GetParent(current)
                      ?? (current as FrameworkElement)?.Parent;
        }
        return null;
    }
}
