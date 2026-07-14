using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views;

public partial class SidebarView : UserControl
{
    /// <summary>Raised when the header collapse chevron is clicked (handled by MainWindow).</summary>
    public event EventHandler? CollapseRequested;

    public SidebarView()
    {
        InitializeComponent();
    }

    private void CollapseButton_Click(object sender, RoutedEventArgs e)
        => CollapseRequested?.Invoke(this, EventArgs.Empty);

    private void SearchButton_Click(object sender, RoutedEventArgs e) => FocusSearch();

    private void CloseSearch_Click(object sender, RoutedEventArgs e) => CollapseSearch();

    private void SearchInput_PreviewKeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == System.Windows.Input.Key.Escape)
        {
            e.Handled = true;
            CollapseSearch();
        }
    }

    /// <summary>Expand the search row into input mode and focus it (click or Ctrl+K).</summary>
    public void FocusSearch()
    {
        if (DataContext is not ConversationListViewModel vm)
            return;
        vm.IsSearchExpanded = true;
        // Focus after the visibility swap has rendered.
        Dispatcher.InvokeAsync(() =>
        {
            SearchInput.Focus();
            SearchInput.CaretIndex = SearchInput.Text.Length;
        }, System.Windows.Threading.DispatcherPriority.Input);
    }

    private void CollapseSearch()
    {
        if (DataContext is ConversationListViewModel vm)
            vm.IsSearchExpanded = false; // also clears SearchText (VM side)
    }

    // ---------- inline rename (web parity R5) ----------

    /// <summary>Focus + select-all when the editor appears with the row (fresh folder).</summary>
    private void InlineRename_Loaded(object sender, RoutedEventArgs e)
    {
        if (sender is TextBox { IsVisible: true } tb)
        {
            tb.Tag = Environment.TickCount64;
            FocusEditor(tb);
        }
    }

    /// <summary>Focus + select-all when an existing row flips into edit mode.</summary>
    private void InlineRename_IsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (sender is TextBox tb && e.NewValue is true)
        {
            tb.Tag = Environment.TickCount64;
            FocusEditor(tb);
        }
    }

    private void FocusEditor(TextBox tb)
    {
        // Background priority: runs AFTER the context menu's own focus
        // restoration, otherwise that restore immediately fires LostFocus on
        // the editor and cancels the rename before the user sees it.
        Dispatcher.InvokeAsync(() =>
        {
            tb.Focus();
            tb.SelectAll();
        }, System.Windows.Threading.DispatcherPriority.Background);
    }

    private async void InlineRename_PreviewKeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (sender is not TextBox tb)
            return;

        if (e.Key == System.Windows.Input.Key.Enter)
        {
            e.Handled = true;
            await CommitInlineRenameAsync(tb);
        }
        else if (e.Key == System.Windows.Input.Key.Escape)
        {
            e.Handled = true;
            // Cancel: leave edit mode without saving. The LostFocus that follows
            // is a no-op because IsRenaming is already false.
            if (tb.DataContext is ConversationItemViewModel conv)
                conv.IsRenaming = false;
            else if (tb.DataContext is FolderItemViewModel folder)
                folder.IsRenaming = false;
        }
    }

    private async void InlineRename_LostFocus(object sender, RoutedEventArgs e)
    {
        if (sender is not TextBox tb)
            return;

        // The closing context menu restores keyboard focus a beat after the
        // editor opens, which would instantly cancel the rename. Within a short
        // grace window, reclaim focus instead of treating it as click-outside.
        if (tb.IsVisible && tb.Tag is long openedAt
            && Environment.TickCount64 - openedAt < 600)
        {
            FocusEditor(tb);
            return;
        }

        await CommitInlineRenameAsync(tb); // click-outside confirms, like the web
    }

    private async Task CommitInlineRenameAsync(TextBox tb)
    {
        if (DataContext is not ConversationListViewModel vm)
            return;
        if (tb.DataContext is ConversationItemViewModel conv)
            await vm.CommitInlineRenameAsync(conv);
        else if (tb.DataContext is FolderItemViewModel folder)
            await vm.CommitFolderInlineRenameAsync(folder);
    }

    /// <summary>
    /// "..." on a conversation or folder row: open the row's ContextMenu below it.
    /// The menu lives on the nearest ancestor that owns one (the row Button, which
    /// also carries the list-VM Tag the menu bindings rely on).
    /// </summary>
    private void Overflow_Click(object sender, RoutedEventArgs e)
    {
        var node = sender as DependencyObject;
        while (node is not null)
        {
            if (node is FrameworkElement { ContextMenu: { } menu } owner)
            {
                menu.PlacementTarget = owner;
                menu.Placement = System.Windows.Controls.Primitives.PlacementMode.Bottom;
                menu.IsOpen = true;
                break;
            }
            node = System.Windows.Media.VisualTreeHelper.GetParent(node);
        }
        e.Handled = true;
    }

    // ---------- drag-drop to folders (Phase 8 Task 2) ----------

    private const string ConversationDragFormat = "SranDesktop.Conversation";

    private Point _dragStart;
    private ConversationItemViewModel? _dragCandidate;
    private Button? _dragSourceButton;

    private static ConversationItemViewModel? RowConversation(object? dataContext) =>
        dataContext switch
        {
            ConversationItemViewModel c => c,
            FolderConversationRow row => row.Conversation,
            _ => null
        };

    private void SidebarList_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        _dragCandidate = null;
        _dragSourceButton = null;
        if (e.OriginalSource is not DependencyObject origin)
            return;
        // Never start a drag from the inline-rename editor.
        if (FindAncestor<TextBox>(origin) is not null)
            return;
        var button = FindAncestor<Button>(origin);
        var conversation = RowConversation(button?.DataContext);
        if (conversation is null || conversation.IsRenaming)
            return;
        _dragCandidate = conversation;
        _dragSourceButton = button;
        _dragStart = e.GetPosition(this);
    }

    private void SidebarList_PreviewMouseMove(object sender, MouseEventArgs e)
    {
        if (_dragCandidate is null || e.LeftButton != MouseButtonState.Pressed)
            return;

        // Only start after the system drag threshold — clicks and inline
        // renames must keep working untouched.
        var pos = e.GetPosition(this);
        if (Math.Abs(pos.X - _dragStart.X) < SystemParameters.MinimumHorizontalDragDistance
            && Math.Abs(pos.Y - _dragStart.Y) < SystemParameters.MinimumVerticalDragDistance)
            return;

        var conversation = _dragCandidate;
        var source = _dragSourceButton;
        _dragCandidate = null;
        _dragSourceButton = null;

        if (source is not null)
            source.Opacity = 0.45; // drag ghost affordance on the source row
        try
        {
            DragDrop.DoDragDrop(this,
                new DataObject(ConversationDragFormat, conversation!),
                DragDropEffects.Move);
        }
        finally
        {
            if (source is not null)
                source.Opacity = 1;
            ClearDropHighlights();
        }
    }

    private ConversationItemViewModel? DraggedConversation(System.Windows.IDataObject data) =>
        data.GetDataPresent(ConversationDragFormat)
            ? data.GetData(ConversationDragFormat) as ConversationItemViewModel
            : null;

    private void FolderRow_DragEnter(object sender, DragEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: FolderItemViewModel folder }
            && DraggedConversation(e.Data) is not null)
            folder.IsDropTarget = true;
    }

    private void FolderRow_DragLeave(object sender, DragEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: FolderItemViewModel folder })
            folder.IsDropTarget = false;
    }

    private void FolderRow_DragOver(object sender, DragEventArgs e)
    {
        e.Effects = DraggedConversation(e.Data) is not null
            ? DragDropEffects.Move
            : DragDropEffects.None;
        e.Handled = true;
    }

    private async void FolderRow_Drop(object sender, DragEventArgs e)
    {
        if (sender is not FrameworkElement { DataContext: FolderItemViewModel folder })
            return;
        folder.IsDropTarget = false;
        var conversation = DraggedConversation(e.Data);
        if (conversation is null || DataContext is not ConversationListViewModel vm)
            return;
        e.Handled = true;

        var decision = SidebarDropDecision.Decide(
            conversation.Id, conversation.FolderId,
            SidebarDropTargetKind.Folder, folder.Id);
        if (decision.ShouldMove)
            await vm.MoveToFolderCommand.ExecuteAsync(
                new MoveToFolderRequest(conversation, decision.TargetFolderId));
    }

    private void HeaderDropZone_DragEnter(object sender, DragEventArgs e)
    {
        var conversation = DraggedConversation(e.Data);
        if (conversation?.FolderId is not null)
            HeaderDropZone.Background =
                (System.Windows.Media.Brush?)TryFindResource("BgSecondaryBrush")
                ?? System.Windows.Media.Brushes.Transparent;
    }

    private void HeaderDropZone_DragLeave(object sender, DragEventArgs e) =>
        HeaderDropZone.Background = System.Windows.Media.Brushes.Transparent;

    private void HeaderDropZone_DragOver(object sender, DragEventArgs e)
    {
        e.Effects = DraggedConversation(e.Data)?.FolderId is not null
            ? DragDropEffects.Move
            : DragDropEffects.None;
        e.Handled = true;
    }

    private async void HeaderDropZone_Drop(object sender, DragEventArgs e)
    {
        HeaderDropZone.Background = System.Windows.Media.Brushes.Transparent;
        var conversation = DraggedConversation(e.Data);
        if (conversation is null || DataContext is not ConversationListViewModel vm)
            return;
        e.Handled = true;

        var decision = SidebarDropDecision.Decide(
            conversation.Id, conversation.FolderId,
            SidebarDropTargetKind.Unfile, targetFolderId: null);
        if (decision.ShouldMove)
            await vm.MoveToFolderCommand.ExecuteAsync(
                new MoveToFolderRequest(conversation, decision.TargetFolderId));
    }

    private void ClearDropHighlights()
    {
        HeaderDropZone.Background = System.Windows.Media.Brushes.Transparent;
        if (DataContext is ConversationListViewModel vm)
        {
            foreach (var folder in vm.Folders)
                folder.IsDropTarget = false;
        }
    }

    private static T? FindAncestor<T>(DependencyObject? node) where T : DependencyObject
    {
        while (node is not null)
        {
            if (node is T match)
                return match;
            node = System.Windows.Media.VisualTreeHelper.GetParent(node);
        }
        return null;
    }

    /// <summary>
    /// Fills the "Move to folder" sub-menu each time a conversation menu opens:
    /// every folder (current one checked + disabled), "New folder…", and
    /// "Remove from folder" when the chat is filed somewhere.
    /// </summary>
    private void ConversationMenu_Opened(object sender, RoutedEventArgs e)
    {
        if (sender is not ContextMenu menu
            || DataContext is not ConversationListViewModel vm
            || menu.DataContext is not ConversationItemViewModel conv)
            return;

        var moveRoot = menu.Items.OfType<MenuItem>()
            .FirstOrDefault(m => Equals(m.Tag, "move-target"));
        if (moveRoot is null)
            return;

        moveRoot.Items.Clear();
        foreach (var folder in vm.Folders)
        {
            var isCurrent = conv.FolderId == folder.Id;
            moveRoot.Items.Add(new MenuItem
            {
                Header = folder.Name,
                IsChecked = isCurrent,
                IsEnabled = !isCurrent,
                Command = vm.MoveToFolderCommand,
                CommandParameter = new MoveToFolderRequest(conv, folder.Id)
            });
        }
        if (vm.Folders.Count > 0)
            moveRoot.Items.Add(new Separator());
        moveRoot.Items.Add(new MenuItem
        {
            Header = "New folder…",
            Command = vm.MoveToNewFolderCommand,
            CommandParameter = conv
        });
        if (conv.FolderId is not null)
        {
            moveRoot.Items.Add(new MenuItem
            {
                Header = "Remove from folder",
                Command = vm.MoveToFolderCommand,
                CommandParameter = new MoveToFolderRequest(conv, null)
            });
        }
    }
}
