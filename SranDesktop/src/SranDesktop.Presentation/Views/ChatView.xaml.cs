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

    // ── Per-conversation scroll memory (Phase 13 Task 3) ────────────────────
    // The id whose position we are currently recording, and whether the newly
    // opened chat still owes us a restore. Both live in the view because the
    // offsets are a property of the rendered list, not of the conversation.
    private string? _trackedConversationId;
    private bool _restorePending;

    private readonly SranDesktop.Application.Services.ScrollMemory _scrollMemory =
        SranDesktop.Application.Services.ScrollMemory.Instance;

    // P2 — a Show more / Show less click changes the extent exactly like
    // stream growth does, and the ScrollChanged re-pin below cannot tell the
    // two apart on its own. The bubble raises CollapseToggled on the click;
    // extent changes inside this grace window skip ONE re-pin so expanding a
    // message while pinned at the bottom does not yank the viewport down.
    private long _collapseToggleTick;
    private const int CollapseToggleGraceMs = 300;

    public ChatView()
    {
        InitializeComponent();
        MessageList.AddHandler(
            Controls.MessageBubble.CollapseToggledEvent,
            new RoutedEventHandler((_, _) => _collapseToggleTick = Environment.TickCount64));
        DataContextChanged += (_, e) =>
        {
            if (e.OldValue is ChatViewModel oldVm)
            {
                oldVm.MessageActivity -= OnMessageActivity;
                oldVm.PropertyChanged -= OnViewModelPropertyChanged;
            }
            if (e.NewValue is ChatViewModel newVm)
            {
                newVm.MessageActivity += OnMessageActivity;
                newVm.PropertyChanged += OnViewModelPropertyChanged;
                OnConversationChanged(newVm);
            }
        };
    }

    private void OnViewModelPropertyChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(ChatViewModel.Conversation) && sender is ChatViewModel vm)
            OnConversationChanged(vm);
    }

    /// <summary>A different chat is now open: stop recording against the old
    /// id and arm a restore for the new one. The old chat's offset was already
    /// captured on its last user scroll, so nothing is read here — by now the
    /// list may have been cleared and its offset reset to 0.</summary>
    private void OnConversationChanged(ChatViewModel vm)
    {
        var next = vm.Conversation?.Id;
        if (next == _trackedConversationId)
            return;
        _trackedConversationId = next;
        _autoScroll = true;                     // new chats start at the newest message
        _restorePending = next is not null && _scrollMemory.Recall(next) is not null;
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
        {
            _autoScroll = scroll.VerticalOffset >= scroll.ScrollableHeight - Services.ScrollStateHelper.BottomThreshold;
            // Remember where the user left this chat. Recorded on the user's
            // own scrolls only — content growth during a stream must not
            // overwrite the place they deliberately scrolled to.
            _scrollMemory.Remember(_trackedConversationId, scroll.VerticalOffset);
            _restorePending = false; // they've taken over; don't yank them later
        }
        else if (_restorePending && TryRestoreScroll(scroll))
        {
            // restored — leave the offset alone this pass
        }
        else if (_autoScroll && !WasCollapseJustToggled())
        {
            scroll.ScrollToVerticalOffset(scroll.ExtentHeight);
        }

        UpdateJumpToLatest(scroll);
    }

    /// <summary>Puts the freshly rendered transcript back where the user left
    /// it. Returns false when there is nothing to restore (or the remembered
    /// place was the bottom), leaving the normal auto-stick in charge.</summary>
    private bool TryRestoreScroll(ScrollViewer scroll)
    {
        var target = SranDesktop.Application.Services.ScrollRestore.Resolve(
            _scrollMemory.Recall(_trackedConversationId),
            scroll.ScrollableHeight,
            Services.ScrollStateHelper.BottomThreshold);
        if (target is not { } offset)
        {
            // Only stop trying once the list actually has scrollable content;
            // the first pass of a long transcript can still be laying out.
            if (scroll.ScrollableHeight > 0)
                _restorePending = false;
            return false;
        }

        scroll.ScrollToVerticalOffset(offset);
        _autoScroll = false; // the user's place wins over following the stream
        _restorePending = false;
        return true;
    }

    /// <summary>Phase C PART 2: show the jump button only when scrolled up with
    /// content below (even during a stream that keeps growing).</summary>
    private void UpdateJumpToLatest(ScrollViewer scroll)
    {
        var hasContentBelow = scroll.ScrollableHeight > 0
            && scroll.VerticalOffset < scroll.ScrollableHeight - Services.ScrollStateHelper.BottomThreshold;
        JumpToLatestButton.Visibility =
            Services.ScrollStateHelper.ShouldShowJumpToLatest(_autoScroll, hasContentBelow)
                ? Visibility.Visible : Visibility.Collapsed;
    }

    private bool WasCollapseJustToggled() =>
        _collapseToggleTick != 0
        && Environment.TickCount64 - _collapseToggleTick < CollapseToggleGraceMs;

    private void JumpToLatest_Click(object sender, RoutedEventArgs e)
    {
        var scroll = MessageScroll;
        if (scroll is null) return;
        _autoScroll = true; // re-pin so the stream keeps following
        // Explicitly asking for the newest message clears the remembered
        // place — otherwise reopening this chat would scroll back up again.
        _scrollMemory.Forget(_trackedConversationId);
        _restorePending = false;
        Services.AnimatedScroller.ScrollToBottom(scroll); // snaps when reduced-motion
        JumpToLatestButton.Visibility = Visibility.Collapsed;
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
