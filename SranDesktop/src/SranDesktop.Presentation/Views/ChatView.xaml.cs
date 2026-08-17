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

    // ── P3 — send-time turn anchoring ───────────────────────────────────────
    // On the user's own send (UserTurnStarted), the new turn scrolls near the
    // viewport top with AnchorPreviousPeekPx of the previous message peeking
    // above it, and a dynamic spacer (extra bottom padding on MessageList —
    // a CONTROL property, so container recycling can't reset it) makes that
    // reachable in short threads. While _anchorActive, streaming does NOT
    // drag the view; once the reply fills the viewport the pre-existing
    // ScrollToEnd follow takes over. Any real user scroll cancels the hold.

    /// <summary>Sliver of the PREVIOUS message kept visible above the anchored
    /// turn — THE one tuning knob (QA: try e.g. viewport/3 here).</summary>
    private const double AnchorPreviousPeekPx = 64;

    private const int AnchorRetryPasses = 5;
    private const int AnchorScrollGraceMs = 150;
    /// <summary>Send happened; the anchor jump is queued behind layout but
    /// has not been applied yet. Distinct from <see cref="_anchorActive"/> so
    /// the MessageActivity that fires immediately after the send cannot run
    /// the hold logic against pre-anchor geometry (an at-bottom send would
    /// satisfy the handover check instantly and kill the anchor).</summary>
    private bool _anchorPending;
    private bool _anchorActive;
    private long _anchorScrollTick;
    private double _spacerPx;
    private Thickness _baseListPadding;

    public ChatView()
    {
        InitializeComponent();
        _baseListPadding = MessageList.Padding;
        MessageList.AddHandler(
            Controls.MessageBubble.CollapseToggledEvent,
            new RoutedEventHandler((_, _) => _collapseToggleTick = Environment.TickCount64));
        DataContextChanged += (_, e) =>
        {
            if (e.OldValue is ChatViewModel oldVm)
            {
                oldVm.MessageActivity -= OnMessageActivity;
                oldVm.UserTurnStarted -= OnUserTurnStarted;
                oldVm.PropertyChanged -= OnViewModelPropertyChanged;
            }
            if (e.NewValue is ChatViewModel newVm)
            {
                newVm.MessageActivity += OnMessageActivity;
                newVm.UserTurnStarted += OnUserTurnStarted;
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
        // P3 — a turn anchor never crosses conversations.
        _anchorPending = false;
        _anchorActive = false;
        SetSpacer(0);
    }

    /// <summary>The ListBox's internal ScrollViewer (materialized after the template applies).</summary>
    private ScrollViewer? MessageScroll =>
        _messageScroll ??= FindDescendant<ScrollViewer>(MessageList);

    private void OnMessageActivity(object? sender, EventArgs e)
    {
        // P3 — while the anchor jump is still queued, do nothing (the old
        // ScrollToEnd here is exactly what the anchor replaces).
        if (_anchorPending)
            return;
        // P3 phase 1 — while a turn is anchored, activity (streamed
        // deltas) shrinks the spacer instead of dragging the view.
        if (_anchorActive)
        {
            HoldAnchor();
            return;
        }
        if (_autoScroll)
            MessageScroll?.ScrollToEnd();
    }

    // ── P3 — turn anchoring ─────────────────────────────────────────────────

    private void OnUserTurnStarted(object? sender, EventArgs e)
    {
        _anchorPending = true;
        _anchorActive = false;
        _autoScroll = false; // extent growth must not re-pin during the hold
        // Anchor after the new containers exist and have a size.
        Dispatcher.InvokeAsync(() => ApplyAnchor(AnchorRetryPasses),
            System.Windows.Threading.DispatcherPriority.Loaded);
    }

    private void ApplyAnchor(int retries)
    {
        if (!_anchorPending) return;
        var scroll = MessageScroll;
        if (scroll is null || DataContext is not ChatViewModel vm)
        {
            _anchorPending = false;
            return;
        }
        var userVm = vm.Messages.LastOrDefault(m => m.IsUser);
        if (userVm is null)
        {
            _anchorPending = false;
            return;
        }
        var gen = MessageList.ItemContainerGenerator;
        if (gen.ContainerFromItem(userVm) is not FrameworkElement container
            || container.ActualHeight <= 0)
        {
            // Virtualized + scrolled far up: the appended row has no
            // container yet. Realize it, then retry next layout pass.
            if (retries <= 0) { _anchorPending = false; return; }
            _anchorScrollTick = Environment.TickCount64;
            MessageList.ScrollIntoView(userVm);
            Dispatcher.InvokeAsync(() => ApplyAnchor(retries - 1),
                System.Windows.Threading.DispatcherPriority.Loaded);
            return;
        }

        var userTop = ContentTopOf(container, scroll);
        double? prevTop = null;
        var idx = vm.Messages.IndexOf(userVm);
        if (idx > 0 && gen.ContainerFromIndex(idx - 1) is FrameworkElement prev)
            prevTop = ContentTopOf(prev, scroll);

        var target = SranDesktop.Application.Services.TurnAnchorMath.AnchorTarget(
            userTop, prevTop, AnchorPreviousPeekPx);
        SetSpacer(SranDesktop.Application.Services.TurnAnchorMath.SpacerNeeded(
            target, scroll.ViewportHeight, scroll.ExtentHeight - _spacerPx));
        _anchorScrollTick = Environment.TickCount64;
        // Instant offset set — the idiom every non-button scroll in this
        // file already uses (AnimatedScroller stays button-only).
        scroll.ScrollToVerticalOffset(target);
        _anchorPending = false;
        _anchorActive = true;
    }

    /// <summary>Phase 1 tick: shrink the spacer as real content replaces it
    /// (never below what the current offset needs, so nothing clamps), and
    /// hand over to the regular follow once the reply fills the viewport —
    /// or the turn ended while still anchored (short reply): keep the view
    /// where it is and just trim the leftover spacer.</summary>
    private void HoldAnchor()
    {
        var scroll = MessageScroll;
        if (scroll is null) return;
        var contentH = scroll.ExtentHeight - _spacerPx;
        var needed = SranDesktop.Application.Services.TurnAnchorMath.SpacerNeeded(
            scroll.VerticalOffset, scroll.ViewportHeight, contentH);
        if (needed < _spacerPx)
            SetSpacer(needed);
        if (DataContext is ChatViewModel { IsStreaming: false })
        {
            _anchorActive = false; // turn over — view stays put
            return;
        }
        // Phase 2 handover — THE policy line to tune.
        if (SranDesktop.Application.Services.TurnAnchorMath.ShouldResumeFollow(
                contentH, scroll.VerticalOffset, scroll.ViewportHeight))
        {
            _anchorActive = false;
            _autoScroll = true;
        }
    }

    /// <summary>A programmatic anchor scroll just happened — its follow-up
    /// ScrollChanged must not be mistaken for the user cancelling the hold.
    /// Same tick-window idiom as the P2 collapse grace.</summary>
    private bool WasAnchorScrollJustIssued() =>
        _anchorScrollTick != 0
        && Environment.TickCount64 - _anchorScrollTick < AnchorScrollGraceMs;

    /// <summary>Content-coordinate top of a realized container (viewport
    /// position + current offset; ScrollUnit is Pixel on this list).</summary>
    private static double ContentTopOf(FrameworkElement container, ScrollViewer scroll)
        => container.TransformToAncestor(scroll).Transform(new Point(0, 0)).Y
           + scroll.VerticalOffset;

    /// <summary>The dynamic bottom spacer = extra bottom padding on the
    /// ListBox itself. A control-level property survives container
    /// recycling, is pure geometry (never content: nothing to copy, export,
    /// announce, or virtualize), and collapses back to the XAML baseline
    /// when the anchor releases it.</summary>
    private void SetSpacer(double px)
    {
        px = Math.Max(0, Math.Round(px));
        if (Math.Abs(px - _spacerPx) < 1) return;
        _spacerPx = px;
        MessageList.Padding = new Thickness(
            _baseListPadding.Left, _baseListPadding.Top,
            _baseListPadding.Right, _baseListPadding.Bottom + px);
    }

    private void MessageList_ScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        var scroll = MessageScroll;
        if (scroll is null) return;

        // A user scroll (no content growth) sets the intent: away from the
        // bottom pauses auto-scroll, returning to the bottom resumes it.
        if (e.ExtentHeightChange == 0)
        {
            // P3 — the user always wins: a real scroll (not the anchor's
            // own programmatic jump) cancels the hold for this turn.
            if (!WasAnchorScrollJustIssued())
            {
                _anchorPending = false;
                _anchorActive = false;
            }
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
