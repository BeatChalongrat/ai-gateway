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

    // The conversation currently displayed — tracked so a switch can be
    // detected and the view landed at the newest turn (rule 3). Scroll
    // POSITION is deliberately not remembered across switches; see
    // OnConversationChanged.
    private string? _trackedConversationId;


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
    /// <summary>Last real input gesture on the message list (wheel / mouse /
    /// key / touch). While a hold is armed, only a ScrollChanged inside this
    /// grace window counts as "the user scrolled" — anything else is system
    /// drift (our own writes' follow-ups, panel corrections) and must neither
    /// cancel the hold nor flip the follow state. This is what let the hold
    /// self-cancel before: the anchor's own event flipped _autoScroll to true
    /// (the anchored offset IS max-scroll thanks to the spacer), the next
    /// delta re-pinned, and the re-pin's follow-up event read as a user
    /// scroll and killed the hold — leaving the spacer stuck at full size.</summary>
    private long _userInputTick;
    private const int UserInputGraceMs = 500;
    private long _holdTraceTick;
    /// <summary>Follow state as it was BEFORE the send armed the anchor —
    /// restored when the anchor cannot be applied, so a failed anchor
    /// degrades to the old ScrollToEnd behaviour instead of a dead turn.</summary>
    private bool _autoScrollBeforeAnchor;
    private long _anchorScrollTick;
    private double _spacerPx;
    /// <summary>The message currently carrying the trailing spacer (always
    /// the LAST message of the anchored turn). VM-side so recycling can't
    /// drop it; cleared before a new host is assigned, so it never
    /// accumulates across turns.</summary>
    private MessageViewModel? _spacerHost;

    public ChatView()
    {
        InitializeComponent();
        MessageList.AddHandler(
            Controls.MessageBubble.CollapseToggledEvent,
            new RoutedEventHandler((_, _) => _collapseToggleTick = Environment.TickCount64));
        // P3 — "the user always wins" needs to know what a USER scroll is.
        // On WPF, ScrollChanged fires for everything (our own offset writes,
        // the extent re-pin's follow-up, VSP corrections), so during a hold
        // we classify by actual input: any of these stamps the tick, and a
        // hold is only cancelled inside its grace window. PreviewMouseMove
        // with a pressed button keeps a scrollbar-thumb drag stamped for its
        // whole duration.
        MessageList.PreviewMouseWheel += (_, _) => _userInputTick = Environment.TickCount64;
        MessageList.PreviewMouseDown += (_, _) => _userInputTick = Environment.TickCount64;
        MessageList.PreviewKeyDown += (_, _) => _userInputTick = Environment.TickCount64;
        MessageList.PreviewMouseMove += (_, e) =>
        {
            if (e.LeftButton == MouseButtonState.Pressed)
                _userInputTick = Environment.TickCount64;
        };
        MessageList.PreviewTouchMove += (_, _) => _userInputTick = Environment.TickCount64;
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

    /// <summary>A different chat is now open: reset the turn anchor and land
    /// at the newest message.</summary>
    private void OnConversationChanged(ChatViewModel vm)
    {
        var next = vm.Conversation?.Id;
        if (next == _trackedConversationId)
            return;
        _trackedConversationId = next;
        // Rule 3: opening or switching to a conversation ALWAYS lands at the
        // newest turn. This replaced a per-conversation scroll memory that
        // restored the previous reading position — switching away and back
        // dropped you mid-thread with no sign that newer messages existed
        // below. "Continue the conversation" is the intent behind opening
        // one; a stale offset is never what you meant.
        _autoScroll = true;
        // P3 — a turn anchor never crosses conversations.
        _anchorPending = false;
        _anchorActive = false;
        ClearSpacer();
        // Land at the bottom as soon as the new list has laid out.
        Dispatcher.InvokeAsync(() => MessageScroll?.ScrollToEnd(),
            System.Windows.Threading.DispatcherPriority.Loaded);
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
        // P3 — web-parity end-trim, hold state irrelevant: if a turn left a
        // spacer behind (e.g. the user genuinely cancelled the hold
        // mid-stream) it is trimmed to what the CURRENT position needs the
        // moment the stream is over — the view never moves, the unused gap
        // goes. Derived, never adjusted, so it cannot stick or accumulate.
        if (_spacerPx > 0 && DataContext is ChatViewModel { IsStreaming: false } restVm)
            TrimSpacerAtRest(restVm);
        if (_autoScroll)
            MessageScroll?.ScrollToEnd();
    }

    /// <summary>Recompute the leftover spacer from current geometry once the
    /// turn is over: the minimum that keeps the current offset valid —
    /// zero whenever the reply content already fills the viewport.</summary>
    private void TrimSpacerAtRest(ChatViewModel vm)
    {
        var scroll = MessageScroll;
        if (scroll is null) return;
        var contentH = scroll.ExtentHeight - _spacerPx;
        var needed = SranDesktop.Application.Services.TurnAnchorMath.SpacerNeeded(
            scroll.VerticalOffset, scroll.ViewportHeight, contentH);
        if (needed < _spacerPx)
        {
            SetSpacer(vm, needed);
            vm.TraceAnchor("trim",
                $"post-turn spacer={needed:F0} (offset={scroll.VerticalOffset:F0} contentH={contentH:F0})");
        }
    }

    // ── P3 — turn anchoring ─────────────────────────────────────────────────

    private void OnUserTurnStarted(object? sender, EventArgs e)
    {
        _autoScrollBeforeAnchor = _autoScroll;
        _anchorPending = true;
        _anchorActive = false;
        _autoScroll = false; // extent growth must not re-pin during the hold
        (DataContext as ChatViewModel)?.TraceAnchor("armed",
            $"wasAutoScroll={_autoScrollBeforeAnchor}");
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
            AbortAnchor(null, "no ScrollViewer / no ChatViewModel");
            return;
        }
        var userVm = vm.Messages.LastOrDefault(m => m.IsUser);
        if (userVm is null)
        {
            AbortAnchor(vm, "no user message in Messages");
            return;
        }
        var gen = MessageList.ItemContainerGenerator;
        if (gen.ContainerFromItem(userVm) is not FrameworkElement container
            || container.ActualHeight <= 0)
        {
            // Virtualized + scrolled far up: the appended row has no
            // container yet. Realize it, then retry next layout pass.
            if (retries <= 0)
            {
                AbortAnchor(vm, "container never realized (retries exhausted)");
                return;
            }
            vm.TraceAnchor("realize",
                $"container missing, ScrollIntoView + retry ({retries} left)");
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

        // Content height comes from the REALIZED turn, never from
        // ExtentHeight. A VirtualizingStackPanel with recycling does not
        // report an extent that includes a row until that row is realized —
        // and the row is exactly what is missing when the user has scrolled
        // away, which is when the anchor matters most. Waiting for the extent
        // aborted 1 send in 3 (audit: 10 applied / 5 "extent never ready"),
        // and each retry recomputed the spacer against a stale extent, so it
        // GREW every pass (175→350→525→700→875) instead of converging.
        //
        // The bottom of the last realized item is always obtainable and is
        // the same quantity web feeds its spacerNeeded (scrollHeight minus
        // the spacer). Its own ActualHeight includes the spacer it hosts, so
        // that is subtracted back out.
        var contentHeight = ContentHeightOfTurn(vm, gen, scroll, container, userTop);
        var spacer = SranDesktop.Application.Services.TurnAnchorMath.SpacerNeeded(
            target, scroll.ViewportHeight, contentHeight);
        vm.TraceAnchor("measure",
            $"userTop={userTop:F0} prevTop={prevTop?.ToString("F0") ?? "-"} target={target:F0} " +
            $"contentH={contentHeight:F0} viewport={scroll.ViewportHeight:F0} " +
            $"offset={scroll.VerticalOffset:F0} spacer={spacer:F0}");
        SetSpacer(vm, spacer);

        // The spacer only exists after a layout pass — without this, the
        // offset below is coerced against the OLD extent and the view
        // silently refuses to move (the original P3-desktop bug).
        MessageList.UpdateLayout();

        _anchorScrollTick = Environment.TickCount64;
        // Animated: sending is a deliberate request to see the new turn, and
        // AnimatedScroller falls back to a snap when MotionSettings is off.
        SranDesktop.Presentation.Services.AnimatedScroller.ScrollTo(scroll, target);
        _anchorPending = false;
        _anchorActive = true;
        vm.TraceAnchor("applied",
            $"target={target:F0} extentAfterLayout={scroll.ExtentHeight:F0} offsetNow={scroll.VerticalOffset:F0}");
    }

    /// <summary>
    /// Height of the content excluding the turn spacer, measured from
    /// realized containers. Prefers the LAST item (the streaming reply);
    /// falls back to the user's own row, which is realized by construction
    /// because ApplyAnchor just scrolled it into view. Any small
    /// under-estimate self-corrects: HoldAnchor shrinks the spacer as the
    /// reply arrives.
    /// </summary>
    private double ContentHeightOfTurn(
        ChatViewModel vm,
        System.Windows.Controls.ItemContainerGenerator gen,
        ScrollViewer scroll,
        FrameworkElement userContainer,
        double userTop)
    {
        var last = vm.Messages.Count > 0 ? vm.Messages[vm.Messages.Count - 1] : null;
        if (last is not null
            && gen.ContainerFromItem(last) is FrameworkElement lastContainer
            && lastContainer.ActualHeight > 0)
        {
            // ActualHeight includes the spacer this item hosts — take it back
            // out so the value is pure content, like web's scrollHeight - spacer.
            return ContentTopOf(lastContainer, scroll) + lastContainer.ActualHeight - _spacerPx;
        }

        return userTop + userContainer.ActualHeight;
    }

    /// <summary>
    /// Anchor could not be applied at all (no ScrollViewer, no view model, no
    /// user message — never a geometry wait, which no longer exists). Log WHY
    /// and stop.
    ///
    /// Deliberately does NOT fall back to ScrollToEnd. The two land in the
    /// same place only when the spacer is right, so a ScrollToEnd fallback
    /// would silently paper over exactly the bug this path exists to reveal;
    /// an anchor that fails visibly is worth more than a scroll that hides
    /// the failure. It also restores follow state rather than forcing it: the
    /// old behaviour here left a scrolled-up user's view frozen on send,
    /// which was the original complaint.
    /// </summary>
    private void AbortAnchor(ChatViewModel? vm, string reason)
    {
        _anchorPending = false;
        _anchorActive = false;
        _autoScroll = _autoScrollBeforeAnchor;
        vm?.TraceAnchor("ABORT", $"{reason}; restored autoScroll={_autoScroll}");
    }

    /// <summary>Phase 1 tick: shrink the spacer as real content replaces it
    /// (never below what the current offset needs, so nothing clamps), and
    /// hand over to the regular follow once the reply fills the viewport —
    /// or the turn ended while still anchored (short reply): keep the view
    /// where it is and just trim the leftover spacer.</summary>
    private void HoldAnchor()
    {
        var scroll = MessageScroll;
        if (scroll is null || DataContext is not ChatViewModel vm) return;
        var contentH = scroll.ExtentHeight - _spacerPx;
        var needed = SranDesktop.Application.Services.TurnAnchorMath.SpacerNeeded(
            scroll.VerticalOffset, scroll.ViewportHeight, contentH);
        if (needed < _spacerPx)
        {
            SetSpacer(vm, needed);
            // Shrink-path trace (≤1/s): without this line the whole hold
            // phase was invisible in the log — a stage that never logs
            // cannot be diagnosed remotely.
            if (!SranDesktop.Application.Services.TurnAnchorMath.IsRecentTick(
                    Environment.TickCount64, _holdTraceTick, 1000))
            {
                _holdTraceTick = Environment.TickCount64;
                vm.TraceAnchor("hold",
                    $"spacer={needed:F0} contentH={contentH:F0} offset={scroll.VerticalOffset:F0} extent={scroll.ExtentHeight:F0}");
            }
        }
        if (!vm.IsStreaming)
        {
            _anchorActive = false; // turn over — view stays put
            vm.TraceAnchor("hold-end", $"turn finished; spacer={_spacerPx:F0} offset={scroll.VerticalOffset:F0}");
            return;
        }
        // Phase 2 handover — THE policy line to tune.
        if (SranDesktop.Application.Services.TurnAnchorMath.ShouldResumeFollow(
                contentH, scroll.VerticalOffset, scroll.ViewportHeight))
        {
            _anchorActive = false;
            _autoScroll = true;
            vm.TraceAnchor("resume",
                $"content filled viewport (contentH={contentH:F0} offset={scroll.VerticalOffset:F0} viewport={scroll.ViewportHeight:F0})");
        }
    }

    /// <summary>A programmatic anchor scroll just happened — its follow-up
    /// ScrollChanged must not be mistaken for the user cancelling the hold.
    /// Same tick-window idiom as the P2 collapse grace; the predicate itself
    /// is headless (TurnAnchorMath.IsRecentTick).</summary>
    private bool WasAnchorScrollJustIssued() =>
        SranDesktop.Application.Services.TurnAnchorMath.IsRecentTick(
            Environment.TickCount64, _anchorScrollTick, AnchorScrollGraceMs);

    /// <summary>Content-coordinate top of a realized container (viewport
    /// position + current offset; ScrollUnit is Pixel on this list).</summary>
    private static double ContentTopOf(FrameworkElement container, ScrollViewer scroll)
        => container.TransformToAncestor(scroll).Transform(new Point(0, 0)).Y
           + scroll.VerticalOffset;

    /// <summary>The dynamic bottom spacer, hosted as TurnSpacerHeight on the
    /// LAST message of the turn and rendered by the item template as an empty
    /// Border below the bubble — i.e. INSIDE the scrolled content, so the
    /// VirtualizingStackPanel measures it into ExtentHeight. (The first
    /// implementation used ListBox.Padding, which template-binds to the inner
    /// ScrollViewer's viewport chrome: it never entered the extent, every
    /// anchor offset was coerced short, and the view "refused" to move.)
    /// VM-hosted, so container recycling can't reset it; re-hosting clears
    /// the previous carrier, so it never accumulates across turns.</summary>
    private void SetSpacer(ChatViewModel vm, double px)
    {
        px = Math.Max(0, Math.Round(px));
        var host = vm.Messages.Count > 0 ? vm.Messages[vm.Messages.Count - 1] : null;
        if (!ReferenceEquals(_spacerHost, host) && _spacerHost is not null)
            _spacerHost.TurnSpacerHeight = 0;
        _spacerHost = host;
        _spacerPx = host is null ? 0 : px;
        if (host is not null)
            host.TurnSpacerHeight = px;
    }

    private void ClearSpacer()
    {
        if (_spacerHost is not null)
            _spacerHost.TurnSpacerHeight = 0;
        _spacerHost = null;
        _spacerPx = 0;
    }

    private void MessageList_ScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        var scroll = MessageScroll;
        if (scroll is null) return;

        // A user scroll (no content growth) sets the intent: away from the
        // bottom pauses auto-scroll, returning to the bottom resumes it.
        if (e.ExtentHeightChange == 0)
        {
            // P3 — while a hold is armed, this branch runs ONLY for real user
            // input. Everything else (the anchor's own follow-up, a re-pin's
            // follow-up, panel corrections) is system drift: it must not
            // cancel the hold, must not recompute _autoScroll (the anchored
            // offset IS max-scroll, so the old recompute flipped it true and
            // started the self-cancel loop), and must not pollute scroll
            // memory with offsets nobody chose.
            if (_anchorPending || _anchorActive)
            {
                if (WasAnchorScrollJustIssued() ||
                    !SranDesktop.Application.Services.TurnAnchorMath.IsRecentTick(
                        Environment.TickCount64, _userInputTick, UserInputGraceMs))
                {
                    UpdateJumpToLatest(scroll);
                    return; // drift — the hold stands, follow state untouched
                }
                (DataContext as ChatViewModel)?.TraceAnchor("user-cancel",
                    $"offset={scroll.VerticalOffset:F0} pending={_anchorPending} active={_anchorActive}");
                _anchorPending = false;
                _anchorActive = false;
            }
            _autoScroll = scroll.VerticalOffset >= scroll.ScrollableHeight - Services.ScrollStateHelper.BottomThreshold;
        }
        else if (_autoScroll && !WasCollapseJustToggled())
        {
            scroll.ScrollToVerticalOffset(scroll.ExtentHeight);
        }

        UpdateJumpToLatest(scroll);
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
