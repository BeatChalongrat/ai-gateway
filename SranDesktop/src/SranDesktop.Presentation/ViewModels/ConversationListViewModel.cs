using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Net.Http;
using System.Windows.Data;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Move a conversation into a folder (null FolderId = unfile).</summary>
public sealed record MoveToFolderRequest(ConversationItemViewModel Conversation, string? FolderId);

public partial class ConversationListViewModel : ObservableObject
{
    private readonly IChatEdgeApiClient _api;
    private readonly IConversationStore _store;
    private readonly IConversationSyncService _syncService;
    private readonly IAgentConversationSyncService _agentSync;
    private readonly ISettingsService _settings;
    private readonly ILogger<ConversationListViewModel> _logger;

    public ObservableCollection<ConversationItemViewModel> Conversations { get; } = new();

    /// <summary>Grouped (Pinned/Today/Yesterday/…) and search-filtered view for the sidebar.
    /// Conversations filed in a known folder are excluded — they render in their folder section.</summary>
    public ICollectionView ConversationsView { get; }

    /// <summary>Sidebar chat folders (server-side, migration 022), ordered by position.</summary>
    public ObservableCollection<FolderItemViewModel> Folders { get; } = new();

    [ObservableProperty]
    private string _searchText = string.Empty;

    /// <summary>Search row mode: false = "Search chats" button, true = inline input (web parity).</summary>
    [ObservableProperty]
    private bool _isSearchExpanded;

    [ObservableProperty]
    private ConversationItemViewModel? _selectedConversation;

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private string? _statusMessage;

    /// <summary>Model used when creating new conversations (until a model picker ships).</summary>
    public string DefaultModelId { get; private set; }

    public event EventHandler<ConversationItemViewModel?>? ConversationSelected;

    /// <summary>Set by MainViewModel: true when the open chat has no messages yet.</summary>
    public Func<bool>? IsCurrentChatEmpty { get; set; }

    /// <summary>Set by MainViewModel: focus the composer instead of spawning a duplicate blank chat.</summary>
    public Action? FocusComposer { get; set; }

    public ConversationListViewModel(
        IChatEdgeApiClient api,
        IConversationStore store,
        IConversationSyncService syncService,
        IAgentConversationSyncService agentSync,
        ISettingsService settings,
        ILogger<ConversationListViewModel> logger)
    {
        _api = api;
        _store = store;
        _syncService = syncService;
        _agentSync = agentSync;
        _settings = settings;
        _logger = logger;
        DefaultModelId = settings.Current.DefaultModel;
        settings.SettingsChanged += (_, _) => DefaultModelId = settings.Current.DefaultModel;

        var view = new ListCollectionView(Conversations)
        {
            Filter = FilterMainList
        };
        view.GroupDescriptions.Add(new PropertyGroupDescription(nameof(ConversationItemViewModel.GroupKey)));
        view.SortDescriptions.Add(new SortDescription(nameof(ConversationItemViewModel.GroupRank), ListSortDirection.Ascending));
        view.SortDescriptions.Add(new SortDescription(nameof(ConversationItemViewModel.UpdatedAt), ListSortDirection.Descending));
        view.IsLiveSorting = true;
        view.IsLiveGrouping = true;
        view.IsLiveFiltering = true;
        view.LiveSortingProperties.Add(nameof(ConversationItemViewModel.GroupRank));
        view.LiveSortingProperties.Add(nameof(ConversationItemViewModel.UpdatedAt));
        view.LiveGroupingProperties.Add(nameof(ConversationItemViewModel.GroupKey));
        view.LiveFilteringProperties.Add(nameof(ConversationItemViewModel.FolderId));
        ConversationsView = view;

        // Phase 8 Task 2: the sidebar body renders SidebarRows (one flat,
        // virtualized list) — rebuild it whenever anything that shapes rows
        // changes. Item-level hooks mirror what the old live-shaping
        // CollectionView listened to.
        Conversations.CollectionChanged += OnConversationsCollectionChanged;
        Folders.CollectionChanged += OnFoldersCollectionChanged;
        MessageSearchResults.CollectionChanged += (_, _) => ScheduleRowsRebuild();
    }

    // ── Flat sidebar rows (Phase 8 Task 2) ─────────────────────────────────

    /// <summary>Heterogeneous rows for the single virtualized sidebar list:
    /// FolderItemViewModel / FolderConversationRow / SidebarGroupHeaderRow /
    /// ConversationItemViewModel / SidebarSearchHeaderRow / hit rows.</summary>
    public ObservableCollection<object> SidebarRows { get; } = new();

    private System.Windows.Threading.DispatcherTimer? _rowsRebuildTimer;
    private readonly HashSet<ConversationItemViewModel> _hookedConversations = new();
    private readonly HashSet<FolderItemViewModel> _hookedFolders = new();

    private void OnConversationsCollectionChanged(
        object? sender, System.Collections.Specialized.NotifyCollectionChangedEventArgs e)
    {
        // Reset carries no OldItems — re-hook from scratch.
        if (e.Action == System.Collections.Specialized.NotifyCollectionChangedAction.Reset)
        {
            foreach (var hooked in _hookedConversations)
                hooked.PropertyChanged -= OnConversationRowPropertyChanged;
            _hookedConversations.Clear();
        }
        foreach (var item in e.OldItems?.OfType<ConversationItemViewModel>() ?? Enumerable.Empty<ConversationItemViewModel>())
        {
            if (_hookedConversations.Remove(item))
                item.PropertyChanged -= OnConversationRowPropertyChanged;
        }
        foreach (var item in Conversations)
        {
            if (_hookedConversations.Add(item))
                item.PropertyChanged += OnConversationRowPropertyChanged;
        }
        ScheduleRowsRebuild();
    }

    private void OnFoldersCollectionChanged(
        object? sender, System.Collections.Specialized.NotifyCollectionChangedEventArgs e)
    {
        if (e.Action == System.Collections.Specialized.NotifyCollectionChangedAction.Reset)
        {
            foreach (var hooked in _hookedFolders)
                hooked.PropertyChanged -= OnFolderRowPropertyChanged;
            _hookedFolders.Clear();
        }
        foreach (var item in e.OldItems?.OfType<FolderItemViewModel>() ?? Enumerable.Empty<FolderItemViewModel>())
        {
            if (_hookedFolders.Remove(item))
                item.PropertyChanged -= OnFolderRowPropertyChanged;
        }
        foreach (var item in Folders)
        {
            if (_hookedFolders.Add(item))
                item.PropertyChanged += OnFolderRowPropertyChanged;
        }
        ScheduleRowsRebuild();
    }

    private void OnConversationRowPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        // The same properties the old ListCollectionView live-shaped on.
        if (e.PropertyName is nameof(ConversationItemViewModel.GroupKey)
            or nameof(ConversationItemViewModel.GroupRank)
            or nameof(ConversationItemViewModel.UpdatedAt)
            or nameof(ConversationItemViewModel.FolderId)
            or nameof(ConversationItemViewModel.Title))
            ScheduleRowsRebuild();
    }

    private void OnFolderRowPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is nameof(FolderItemViewModel.IsExpanded))
            ScheduleRowsRebuild();
    }

    /// <summary>Coalesce bursts (sync repopulates row-by-row) into one rebuild
    /// per 50 ms window. Tests call <see cref="RebuildSidebarRowsNow"/>.</summary>
    private void ScheduleRowsRebuild()
    {
        if (_rowsRebuildTimer is null)
        {
            _rowsRebuildTimer = new System.Windows.Threading.DispatcherTimer(
                System.Windows.Threading.DispatcherPriority.Background)
            {
                Interval = TimeSpan.FromMilliseconds(50)
            };
            _rowsRebuildTimer.Tick += (_, _) =>
            {
                _rowsRebuildTimer!.Stop();
                RebuildSidebarRowsNow();
            };
        }
        _rowsRebuildTimer.Stop();
        _rowsRebuildTimer.Start();
    }

    public void RebuildSidebarRowsNow()
    {
        var rows = SidebarRowsBuilder.Build(
            Folders.ToList(),
            Conversations.ToList(),
            MatchesSearch,
            MessageSearchResults.ToList(),
            showSearchSection: IsSearchingMessages || HasMessageSearchResults);

        SidebarRows.Clear();
        foreach (var row in rows)
            SidebarRows.Add(row);
    }

    partial void OnIsSearchingMessagesChanged(bool value) => ScheduleRowsRebuild();

    partial void OnHasMessageSearchResultsChanged(bool value) => ScheduleRowsRebuild();

    partial void OnSearchTextChanged(string value)
    {
        ConversationsView.Refresh();
        foreach (var folder in Folders)
            folder.RefreshFilter(); // folder header counts stay live
        ScheduleRowsRebuild();
        ScheduleMessageSearch(value);
    }

    // ── Full-text search in message content (Phase 5 Task 5) ───────────────
    // Server FTS over chat.messages_masked — covers server conversations AND
    // Phase 3 mirrors of agent chats. Fire-and-forget with debounce; any
    // failure hides the section silently.

    public ObservableCollection<MessageSearchHitViewModel> MessageSearchResults { get; } = new();

    [ObservableProperty]
    private bool _isSearchingMessages;

    [ObservableProperty]
    private bool _hasMessageSearchResults;

    private System.Windows.Threading.DispatcherTimer? _messageSearchDebounce;
    private CancellationTokenSource? _messageSearchCts;

    private void ScheduleMessageSearch(string query)
    {
        _messageSearchCts?.Cancel(); // stale results must never land over fresh input
        if (query.Trim().Length < 2)
        {
            _messageSearchDebounce?.Stop();
            IsSearchingMessages = false;
            MessageSearchResults.Clear();
            HasMessageSearchResults = false;
            return;
        }

        if (_messageSearchDebounce is null)
        {
            _messageSearchDebounce = new System.Windows.Threading.DispatcherTimer
            {
                Interval = TimeSpan.FromMilliseconds(300)
            };
            _messageSearchDebounce.Tick += (_, _) =>
            {
                _messageSearchDebounce!.Stop();
                _ = RunMessageSearchAsync(SearchText.Trim());
            };
        }
        _messageSearchDebounce.Stop();
        _messageSearchDebounce.Start();
    }

    private async Task RunMessageSearchAsync(string query)
    {
        // Cancel + DISPOSE the superseded source (Phase 10 D5) — it was
        // previously only cancelled, leaking one CTS per search keystroke.
        var previous = _messageSearchCts;
        previous?.Cancel();
        previous?.Dispose();
        var cts = new CancellationTokenSource();
        _messageSearchCts = cts;
        IsSearchingMessages = true;
        try
        {
            var response = await _api.SearchMessagesAsync(query, 20, cts.Token);
            if (cts.IsCancellationRequested || !ReferenceEquals(_messageSearchCts, cts))
                return;

            // Hits carry SERVER conversation ids — resolve each onto a local
            // conversation (direct id, or the local chat behind a Phase 3
            // mirror). Server-only conversations show too (Phase 8 Task 6):
            // clicking pulls them locally before opening.
            var locals = Conversations
                .Select(c => (c.Id, c.ServerId))
                .ToList();
            var rows = new List<MessageSearchHitViewModel>();
            foreach (var hit in response.Results ?? new List<MessageSearchHitDto>())
            {
                var resolution = Application.Services.SearchHitResolver
                    .Resolve(hit.ConversationId, locals);
                var title = string.IsNullOrWhiteSpace(hit.ConversationTitle)
                    ? Localization.LocaleService.Instance.T("search.untitled")
                    : hit.ConversationTitle!;
                var snippet = (hit.Content ?? string.Empty)
                    .Replace('\n', ' ').Replace('\r', ' ').Trim();
                rows.Add(new MessageSearchHitViewModel(
                    resolution.ConversationId, title, snippet,
                    requiresPull: resolution.Action
                        == Application.Services.SearchHitAction.PullThenOpen));
            }

            MessageSearchResults.Clear();
            foreach (var row in rows)
                MessageSearchResults.Add(row);
            HasMessageSearchResults = rows.Count > 0;
        }
        catch (OperationCanceledException)
        {
            // superseded by a newer query — nothing to do
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Message search failed (section hidden)");
            MessageSearchResults.Clear();
            HasMessageSearchResults = false;
        }
        finally
        {
            if (ReferenceEquals(_messageSearchCts, cts))
                IsSearchingMessages = false;
        }
    }

    /// <summary>Row click in the "In messages" section: open the conversation
    /// (the server sends no message id, so there is no jump-to-message).
    /// Phase 8 Task 6: a server-only hit pulls its conversation first —
    /// upserted through the same entity mapping as the sync path so
    /// server_id/source semantics stay consistent — then opens it. Failure
    /// shows an error toast and the row stays for a retry.</summary>
    [RelayCommand]
    private async Task OpenMessageHitAsync(MessageSearchHitViewModel? hit)
    {
        if (hit is null || hit.IsPulling)
            return;

        if (!hit.RequiresPull)
        {
            var local = Conversations.FirstOrDefault(c => c.Id == hit.ConversationId);
            if (local is not null)
                SelectedConversation = local;
            return;
        }

        hit.IsPulling = true;
        try
        {
            var dto = await _api.GetConversationAsync(hit.ConversationId);
            var entity = Application.Services.ConversationSyncService
                .ToEntity(dto, DateTimeOffset.UtcNow);
            await _store.UpsertConversationAsync(entity);

            // A concurrent sync may have landed the row already.
            var item = Conversations.FirstOrDefault(c => c.Id == entity.Id);
            if (item is null)
            {
                item = new ConversationItemViewModel(entity);
                Conversations.Add(item);
            }
            SelectedConversation = item;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Search-hit pull failed for {Id}", hit.ConversationId);
            Application.Services.ToastService.Instance.Show(
                Application.Interfaces.ToastKind.Error, "toast.pullFailed");
        }
        finally
        {
            hit.IsPulling = false;
        }
    }

    /// <summary>Regroup after a locale switch — GroupKey labels are localized.</summary>
    public void RefreshGrouping()
    {
        ConversationsView.Refresh();
        RebuildSidebarRowsNow();
    }

    partial void OnIsSearchExpandedChanged(bool value)
    {
        // Collapsing always clears the filter so the full list comes back.
        if (!value)
            SearchText = string.Empty;
    }

    // ── Folders (web parity round 3) ────────────────────────────────────────

    /// <summary>Rebuilds the folder list from the server, keeping expand state.</summary>
    private async Task LoadFoldersAsync()
    {
        try
        {
            var response = await _api.ListFoldersAsync();
            var expandState = Folders.ToDictionary(f => f.Id, f => f.IsExpanded);

            Folders.Clear();
            foreach (var dto in response.Folders.OrderBy(f => f.Position))
            {
                var folder = new FolderItemViewModel(dto, Conversations, MatchesSearch);
                if (expandState.TryGetValue(folder.Id, out var wasExpanded))
                    folder.IsExpanded = wasExpanded;
                Folders.Add(folder);
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Could not load folders");
        }

        // Main-list filter depends on which folders exist.
        ConversationsView.Refresh();
    }

    [RelayCommand]
    private async Task NewFolderAsync()
    {
        try
        {
            var dto = await _api.CreateFolderAsync("New folder");
            var folder = new FolderItemViewModel(dto, Conversations, MatchesSearch);
            Folders.Add(folder);

            // Web parity: the fresh folder appears with its name in inline edit mode.
            folder.EditingName = folder.Name;
            folder.IsRenaming = true;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to create folder");
            StatusMessage = "Could not create folder - server unreachable";
        }
    }

    /// <summary>Context-menu Rename folder: inline editor in the header row.</summary>
    [RelayCommand]
    private void RenameFolder(FolderItemViewModel? folder)
    {
        if (folder is null)
            return;
        folder.EditingName = folder.Name;
        folder.IsRenaming = true;
    }

    /// <summary>Inline folder-rename confirm (Enter / focus lost). Empty or unchanged = cancel.</summary>
    public async Task CommitFolderInlineRenameAsync(FolderItemViewModel folder)
    {
        if (!folder.IsRenaming)
            return;
        folder.IsRenaming = false;

        var name = folder.EditingName.Trim();
        if (name.Length == 0 || name == folder.Name)
            return;

        try
        {
            var dto = await _api.RenameFolderAsync(folder.Id, name);
            folder.Name = string.IsNullOrWhiteSpace(dto.Name) ? name : dto.Name!;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to rename folder {Id}", folder.Id);
            StatusMessage = "Could not rename folder - server unreachable";
        }
    }

    [RelayCommand]
    private async Task DeleteFolderAsync(FolderItemViewModel? folder)
    {
        if (folder is null)
            return;

        var confirm = System.Windows.MessageBox.Show(
            $"Delete folder \"{folder.Name}\"? Chats inside move back to the main list.",
            "Delete folder",
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Warning);
        if (confirm != System.Windows.MessageBoxResult.Yes)
            return;

        try
        {
            await _api.DeleteFolderAsync(folder.Id);
        }
        catch (HttpRequestException ex) when (ex.StatusCode == System.Net.HttpStatusCode.NotFound)
        {
            // Already gone server-side — proceed with local removal.
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to delete folder {Id}", folder.Id);
            StatusMessage = "Could not delete folder - server unreachable";
            return;
        }

        Folders.Remove(folder);

        // The server unfiles members; mirror that locally so they reappear
        // in the time buckets and survive offline restarts.
        foreach (var conv in Conversations.Where(c => c.FolderId == folder.Id).ToList())
        {
            conv.FolderId = null;
            await _store.UpsertConversationAsync(conv.ToEntity());
        }
        ConversationsView.Refresh();
    }

    [RelayCommand]
    private async Task MoveToFolderAsync(MoveToFolderRequest? request)
    {
        if (request is null)
            return;
        var (conv, folderId) = (request.Conversation, request.FolderId);
        if (conv.FolderId == folderId)
            return;

        try
        {
            await _api.MoveConversationToFolderAsync(conv.Id, folderId);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to move conversation {Id} to folder", conv.Id);
            StatusMessage = "Could not move chat - server unreachable";
            return;
        }

        // Live filtering on FolderId re-homes the row in both sections.
        conv.FolderId = folderId;
        await _store.UpsertConversationAsync(conv.ToEntity());
    }

    /// <summary>Context-menu "New folder…": create a folder, then file the chat into it.</summary>
    [RelayCommand]
    private async Task MoveToNewFolderAsync(ConversationItemViewModel? conv)
    {
        if (conv is null)
            return;
        var name = Views.Dialogs.InputDialog.Show(
            System.Windows.Application.Current?.MainWindow, "New folder", "Folder name:");
        if (string.IsNullOrWhiteSpace(name))
            return;

        try
        {
            var dto = await _api.CreateFolderAsync(name.Trim());
            Folders.Add(new FolderItemViewModel(dto, Conversations, MatchesSearch));
            await MoveToFolderAsync(new MoveToFolderRequest(conv, dto.Id));
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to create folder for move");
            StatusMessage = "Could not create folder - server unreachable";
        }
    }

    partial void OnSelectedConversationChanged(
        ConversationItemViewModel? oldValue, ConversationItemViewModel? newValue)
    {
        // Selection is VM-authoritative: rows live in several ItemsControls
        // (folder sections + time buckets), so each row highlights itself
        // off IsSelected instead of a Selector.
        if (oldValue is not null) oldValue.IsSelected = false;
        if (newValue is not null) newValue.IsSelected = true;
        ConversationSelected?.Invoke(this, newValue);
    }

    /// <summary>Row click in any sidebar section.</summary>
    [RelayCommand]
    private void SelectConversation(ConversationItemViewModel? item)
    {
        if (item is not null)
            SelectedConversation = item;
    }

    private bool MatchesSearch(ConversationItemViewModel vm)
    {
        return string.IsNullOrWhiteSpace(SearchText)
               || vm.DisplayTitle.Contains(SearchText.Trim(), StringComparison.OrdinalIgnoreCase);
    }

    private bool FilterMainList(object item)
    {
        if (item is not ConversationItemViewModel vm)
            return false;
        // Hide chats filed in a folder we know about (they render in that folder's
        // section). Unknown folder ids (offline, stale) fall back to the time buckets
        // so the chat never disappears.
        if (vm.FolderId is not null && Folders.Any(f => f.Id == vm.FolderId))
            return false;
        return MatchesSearch(vm);
    }

    public async Task LoadAsync()
    {
        IsLoading = true;
        StatusMessage = null;
        try
        {
            // Local first for instant paint…
            await RepopulateFromStoreAsync();

            // …then reconcile with the server.
            var synced = await _syncService.SyncFromServerAsync();
            if (synced)
            {
                await RepopulateFromStoreAsync();
                await LoadFoldersAsync();
                await ResolveDefaultModelAsync();
                // Prune leftover empty "New conversation" placeholders so the sidebar
                // matches the web (which never lists never-used chats). Silent: these
                // have zero messages, so there is nothing to lose.
                await CleanupEmptyChatsSilentAsync();
                // Phase 3 D: push local agent conversations to their server
                // mirror (fire-and-forget — sidebar must not wait on it).
                _ = Task.Run(() => _agentSync.SyncAllAsync());
            }
            else
            {
                StatusMessage = "Offline - showing local history";
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to load conversations");
            StatusMessage = "Failed to load conversations";
        }
        finally
        {
            IsLoading = false;
        }
    }

    [RelayCommand]
    private void NewChat()
    {
        // Dedup: if the current chat is already empty (fresh conversation or the
        // empty state), don't do anything jarring — just focus the composer.
        if (IsCurrentChatEmpty?.Invoke() == true)
        {
            FocusComposer?.Invoke();
            return;
        }

        // Web parity: New chat shows the empty state (logo + greeting + centered
        // composer + suggestion chips). Deselecting drives ActiveConversation → null.
        // The real conversation is created lazily on the first message send
        // (ChatViewModel.SendAsync → ConversationRequested → CreateNewConversationAsync).
        SelectedConversation = null;

        // Focus the composer AFTER the empty state has rendered — Background priority
        // runs past the visibility swap, so the focus request lands on the incoming
        // empty-state composer rather than the outgoing conversation one.
        System.Windows.Application.Current?.Dispatcher.InvokeAsync(
            () => FocusComposer?.Invoke(),
            System.Windows.Threading.DispatcherPriority.Background);
    }

    /// <summary>
    /// Creates and selects a new conversation unconditionally (no dedup guard).
    /// Used by the empty-state composer, which always has a message to send.
    /// </summary>
    public async Task CreateNewConversationAsync()
    {
        try
        {
            var dto = await _api.CreateConversationAsync(DefaultModelId);
            var entity = Application.Services.ConversationSyncService.ToEntity(dto, DateTimeOffset.UtcNow);
            await _store.UpsertConversationAsync(entity);

            var item = new ConversationItemViewModel(entity);
            Conversations.Insert(0, item);
            SelectedConversation = item;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to create conversation");
            StatusMessage = "Could not create a chat - server unreachable";
        }
    }

    /// <summary>
    /// Untitled / placeholder-titled conversations with no local messages — provably
    /// never used (the server auto-titles a chat after its first exchange). Never
    /// includes the selected chat. Shared by the manual button and the silent startup prune.
    /// </summary>
    private async Task<List<ConversationItemViewModel>> FindEmptyChatsAsync()
    {
        var candidates = new List<ConversationItemViewModel>();
        foreach (var conv in Conversations.ToList())
        {
            if (conv == SelectedConversation)
                continue;
            // Only untitled / placeholder-titled chats are eligible.
            if (!string.IsNullOrWhiteSpace(conv.Title)
                && !conv.Title.Equals("New conversation", StringComparison.OrdinalIgnoreCase))
                continue;
            var messages = await _store.GetMessagesAsync(conv.Id);
            if (messages.Count == 0)
                candidates.Add(conv);
        }
        return candidates;
    }

    /// <summary>
    /// Deletes empty placeholder chats without prompting — safe because they hold no
    /// messages. Runs once after the initial sync to keep the sidebar tidy (web parity).
    /// Best-effort: never surfaces an error or blocks startup.
    /// </summary>
    private async Task CleanupEmptyChatsSilentAsync()
    {
        try
        {
            var candidates = await FindEmptyChatsAsync();
            foreach (var conv in candidates)
            {
                try
                {
                    if (conv.Source == ConversationSource.Server)
                        await _api.DeleteConversationAsync(conv.Id);
                }
                catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
                {
                    // Server-side delete failed (offline / already gone) — still drop it locally.
                }

                await _store.DeleteConversationAsync(conv.Id);
                Conversations.Remove(conv);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Silent empty-chat cleanup failed");
        }
    }

    [RelayCommand]
    private async Task CleanupEmptyChatsAsync()
    {
        var candidates = await FindEmptyChatsAsync();

        if (candidates.Count == 0)
        {
            System.Windows.MessageBox.Show(
                "No empty conversations to clean up.",
                "Clean up empty chats",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Information);
            return;
        }

        var confirm = System.Windows.MessageBox.Show(
            $"Delete {candidates.Count} empty conversation(s)? This cannot be undone.",
            "Clean up empty chats",
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Warning);
        if (confirm != System.Windows.MessageBoxResult.Yes)
            return;

        var removed = 0;
        foreach (var conv in candidates)
        {
            try
            {
                if (conv.Source == ConversationSource.Server)
                    await _api.DeleteConversationAsync(conv.Id);
            }
            catch (HttpRequestException ex) when (ex.StatusCode == System.Net.HttpStatusCode.NotFound)
            {
                // Already gone server-side — proceed with local removal.
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
            {
                _logger.LogWarning(ex, "Could not delete conversation {Id} server-side", conv.Id);
            }

            await _store.DeleteConversationAsync(conv.Id);
            Conversations.Remove(conv);
            removed++;
        }

        StatusMessage = $"Removed {removed} empty chat(s)";
    }

    /// <summary>Context-menu Rename: turn the row title into an inline editor (web parity).</summary>
    [RelayCommand]
    private void Rename(ConversationItemViewModel? item)
    {
        if (item is null)
            return;
        item.EditingName = item.DisplayTitle;
        item.IsRenaming = true;
    }

    /// <summary>Inline-rename confirm (Enter / focus lost). Empty or unchanged = cancel.</summary>
    public async Task CommitInlineRenameAsync(ConversationItemViewModel item)
    {
        if (!item.IsRenaming)
            return; // already committed or cancelled (Enter fires before LostFocus)
        item.IsRenaming = false;

        var newTitle = item.EditingName.Trim();
        if (newTitle.Length == 0 || newTitle == item.DisplayTitle)
            return;
        await CommitRenameAsync(item, newTitle);
    }

    [RelayCommand]
    private async Task DeleteAsync(ConversationItemViewModel? item)
    {
        if (item is null) return;

        var confirm = System.Windows.MessageBox.Show(
            $"Delete \"{item.DisplayTitle}\"? This cannot be undone.",
            "Delete chat",
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Warning);
        if (confirm != System.Windows.MessageBoxResult.Yes)
            return;

        try
        {
            if (item.Source == ConversationSource.Server)
                await _api.DeleteConversationAsync(item.Id);
        }
        catch (HttpRequestException ex) when (ex.StatusCode == System.Net.HttpStatusCode.NotFound)
        {
            // Already gone server-side — proceed with local removal.
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to delete conversation {Id}", item.Id);
            StatusMessage = "Could not delete - server unreachable";
            return;
        }

        // Phase 3 D: a local agent conversation may have a server backup
        // mirror — delete it too, best-effort (never blocks the local delete).
        if (item.Source == ConversationSource.Local)
        {
            try
            {
                var entity = await _store.GetConversationAsync(item.Id);
                if (entity?.ServerId is { Length: > 0 } mirrorId)
                    await _api.DeleteConversationAsync(mirrorId);
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
            {
                _logger.LogDebug(ex, "Mirror delete skipped for {Id}", item.Id);
            }
        }

        await _store.DeleteConversationAsync(item.Id);
        Conversations.Remove(item);
        if (SelectedConversation == item)
            SelectedConversation = null;
    }

    [RelayCommand]
    private async Task TogglePinAsync(ConversationItemViewModel? item)
    {
        if (item is null) return;

        var newValue = !item.IsPinned;
        try
        {
            if (item.Source == ConversationSource.Server)
                await _api.SetConversationPinnedAsync(item.Id, newValue);

            item.IsPinned = newValue;
            await _store.UpsertConversationAsync(item.ToEntity());
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to toggle pin for {Id}", item.Id);
            StatusMessage = "Could not update pin - server unreachable";
        }
    }

    /// <summary>Invoked by the view after collecting the new title from the rename dialog.</summary>
    public async Task CommitRenameAsync(ConversationItemViewModel item, string newTitle)
    {
        newTitle = newTitle.Trim();
        if (newTitle.Length == 0 || newTitle == item.Title)
            return;

        try
        {
            if (item.Source == ConversationSource.Server)
                await _api.RenameConversationAsync(item.Id, newTitle);

            item.Title = newTitle;
            await _store.UpsertConversationAsync(item.ToEntity());
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to rename {Id}", item.Id);
            StatusMessage = "Could not rename - server unreachable";
        }
    }

    public void Clear()
    {
        Conversations.Clear();
        Folders.Clear();
        SelectedConversation = null;
        SearchText = string.Empty;
        StatusMessage = null;
    }

    private async Task RepopulateFromStoreAsync()
    {
        var selectedId = SelectedConversation?.Id;
        var entities = await _store.GetConversationsAsync();

        Conversations.Clear();
        foreach (var entity in entities)
            Conversations.Add(new ConversationItemViewModel(entity));

        if (selectedId is not null)
            SelectedConversation = Conversations.FirstOrDefault(c => c.Id == selectedId);
    }

    private async Task ResolveDefaultModelAsync()
    {
        try
        {
            var response = await _api.ListModelsAsync();
            var chatModels = response.Models
                .Where(m => m.Allowed && (m.ModelKind is null or "chat"))
                .ToList();
            if (chatModels.Count == 0)
                return;

            if (!chatModels.Any(m => m.Id == DefaultModelId))
            {
                DefaultModelId = chatModels[0].Id;
                _logger.LogInformation(
                    "Configured default model '{Configured}' not available; using '{Actual}'",
                    _settings.Current.DefaultModel, DefaultModelId);
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Could not fetch model list; keeping configured default");
        }
    }
}
