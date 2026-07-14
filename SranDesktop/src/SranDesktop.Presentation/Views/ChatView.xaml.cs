using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

public partial class ChatView : UserControl
{
    private bool _autoScroll = true;
    private ScrollViewer? _messageScroll;

    public ChatView()
    {
        InitializeComponent();
        DataContextChanged += (_, e) =>
        {
            if (e.OldValue is ChatViewModel oldVm)
                oldVm.MessageActivity -= OnMessageActivity;
            if (e.NewValue is ChatViewModel newVm)
                newVm.MessageActivity += OnMessageActivity;
        };
    }

    /// <summary>The ListBox's internal ScrollViewer (materialized after the template applies).</summary>
    private ScrollViewer? MessageScroll =>
        _messageScroll ??= FindDescendant<ScrollViewer>(MessageList);

    private void OnMessageActivity(object? sender, EventArgs e)
    {
        if (_autoScroll)
            MessageScroll?.ScrollToEnd();
    }

    private void MessageList_ScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        var scroll = MessageScroll;
        if (scroll is null) return;

        // A user scroll (no content growth) sets the intent: away from the
        // bottom pauses auto-scroll, returning to the bottom resumes it.
        if (e.ExtentHeightChange == 0)
            _autoScroll = scroll.VerticalOffset >= scroll.ScrollableHeight - 4;
        else if (_autoScroll)
            scroll.ScrollToVerticalOffset(scroll.ExtentHeight);
    }

    private static T? FindDescendant<T>(DependencyObject root) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is T match)
                return match;
            if (FindDescendant<T>(child) is { } nested)
                return nested;
        }
        return null;
    }

    private void ChatRoot_DragEnter(object sender, System.Windows.DragEventArgs e)
    {
        if (e.Data.GetDataPresent(System.Windows.DataFormats.FileDrop)
            && DataContext is ChatViewModel { Conversation: not null })
            DropOverlay.Visibility = System.Windows.Visibility.Visible;
    }

    private void ChatRoot_DragLeave(object sender, System.Windows.DragEventArgs e)
    {
        // Only hide when the cursor actually leaves the control bounds (not a child hop).
        var pos = e.GetPosition(this);
        if (pos.X < 0 || pos.Y < 0 || pos.X > ActualWidth || pos.Y > ActualHeight)
            DropOverlay.Visibility = System.Windows.Visibility.Collapsed;
    }

    private void ChatRoot_DragOver(object sender, System.Windows.DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(System.Windows.DataFormats.FileDrop)
            ? System.Windows.DragDropEffects.Copy
            : System.Windows.DragDropEffects.None;
        e.Handled = true;
    }

    private void ChatRoot_Drop(object sender, System.Windows.DragEventArgs e)
    {
        DropOverlay.Visibility = System.Windows.Visibility.Collapsed;
        if (DataContext is not ChatViewModel vm || vm.Conversation is null)
            return;
        if (e.Data.GetData(System.Windows.DataFormats.FileDrop) is string[] paths && paths.Length > 0)
            _ = vm.AttachFilesAsync(paths.Where(System.IO.File.Exists));
        e.Handled = true;
    }
}
