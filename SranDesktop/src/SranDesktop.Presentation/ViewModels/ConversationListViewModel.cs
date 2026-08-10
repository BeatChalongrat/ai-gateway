using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Net.Http;
using System.Windows.Data;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Move a conversation into a project (null ProjectId = unfile).</summary>
public sealed record MoveToProjectRequest(ConversationItemViewModel Conversation, string? ProjectId);

public partial class ConversationListViewModel : ObservableObject
{
    private readonly IChatEdgeApiClient _api;
    private readonly IConversationStore _store;
    private readonly IConversationSyncService _syncService;
    private readonly IAgentConversationSyncService _agentSync;
    private readonly ISettingsService _settings;
    private readonly IDialogService _dialogs;
    private readonly IConversationActivityTracker? _activity;
    private readonly ILogger<ConversationListViewModel> _logger;

    public ObservableCollection<ConversationItemViewModel> Conversations { get; } = new();

    /// <summary>Grouped (Pinned/Today/Yesterday/…) and search-filtered view for the sidebar.
    /// Conversations filed in a known project are excluded — they render in their project section.</summary>
    public ICollectionView ConversationsView { get; }

    /// <summary>Sidebar chat projects (server-side, migration 022), ordered by position.</summary>
    public ObservableCollection<ProjectItemViewModel> Projects { get; } = new();

    [ObservableProperty]
    private string _searchText = string.Empty;

    /// <summary>Search row mode: false = "Search chats" button, true = inline input (web parity).</summary>
    [ObservableProperty]
    private bool _isSearchExpanded;

    [ObservableProperty]
    private ConversationItemViewModel? _selectedConversation;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ShowListSkeleton))]
    [NotifyPropertyChangedFor(nameof(ShowArchiveEmptyState))]
    private bool _isLoading;

    /// <summary>
    /// Phase 14 — the sidebar is showing the ARCHIVE instead of the live chat
    /// list. One list, two modes (rather than a second window) so every row
    /// behaviour — select, rename, open, delete — works unchanged in there.
    /// </summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsMainView))]
    [NotifyPropertyChangedFor(nameof(ShowArchiveEmptyState))]
    private bool _isArchiveView;

    /// <summary>Convenience inverse for XAML (New-chat / search hide in the archive).</summary>
    public bool IsMainView => !IsArchiveView;

    partial void OnIsArchiveViewChanged(bool value)
    {
        ConversationsView.Refresh();
        RebuildSidebarRowsNow();
    }

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
        IDialogService dialogs,
        ILogger<ConversationListViewModel> logger,
        IConversationActivityTracker? activity = null)
    {
        _api = api;
        _store = store;
        _syncService = syncService;
        _agentSync = agentSync;
        _settings = settings;
        _dialogs = dialogs;
        _activity = activity;
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
        view.LiveFilteringProperties.Add(nameof(ConversationItemViewModel.ProjectId));
        ConversationsView = view;

        // Phase 8 Task 2: the sidebar body renders SidebarRows (one flat,
        // virtualized list) — rebuild it whenever anything that shapes rows
        // changes. Item-level hooks mirror what the old live-shaping
        // CollectionView listened to.
        Conversations.CollectionChanged += OnConversationsCollectionChanged;
        Projects.CollectionChanged += OnProjectsCollectionChanged;
        MessageSearchResults.CollectionChanged += (_, _) => ScheduleRowsRebuild();

        // FIX 9 Part C: a push that finds the conversation deleted on the
        // server (deleted in web-chat) removes the local copy — drop the
        // sidebar row too. Both VMs are singletons; no unsubscribe needed.
        _agentSync.RemoteConversationDeleted += OnRemoteConversationDeleted;
    }

    /// <summary>Server-side delete discovered mid-session — reflect it in the
    /// sidebar (the local store row is already gone). Marshalled to the UI
    /// thread: the event can fire on a sync continuation thread.</summary>
    private void OnRemoteConversationDeleted(object? sender, string conversationId)
    {
        void Remove()
        {
            var item = Conversations.FirstOrDefault(c => c.Id == conversationId);
            if (item is null)
                return;
            Conversations.Remove(item);
            if (SelectedConversation == item)
                SelectedConversation = null;
        }

        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is null || dispatcher.CheckAccess())
            Remove();
        else
            dispatcher.InvokeAsync(Remove);
    }

    // ── Flat sidebar rows (Phase 8 Task 2) ─────────────────────────────────

    /// <summary>Heterogeneous rows for the single virtualized sidebar list:
    /// ProjectItemViewModel / ProjectConversationRow / SidebarGroupHeaderRow /
    /// ConversationItemViewModel / SidebarSearchHeaderRow / hit rows.</summary>
    public ObservableCollection<object> SidebarRows { get; } = new();

    /// <summary>
    /// Phase 13 Task 3 — placeholder rows while the very first load runs.
    /// Gated on the list being EMPTY, so a background refresh of an already
    /// populated sidebar never replaces the user's chats with grey bars.
    /// </summary>
    public bool ShowListSkeleton => IsLoading && SidebarRows.Count == 0;

    /// <summary>Phase 14 — "nothing archived yet". Only in the archive view,
    /// and never while the list is still loading (that is the skeleton's job).</summary>
    public bool ShowArchiveEmptyState => IsArchiveView && !IsLoading && SidebarRows.Count == 0;

    private System.Windows.Threading.DispatcherTimer? _rowsRebuildTimer;
    private readonly HashSet<ConversationItemViewModel> _hookedConversations = new();
    private readonly HashSet<ProjectItemViewModel> _hookedProjects = new();

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

    private void OnProjectsCollectionChanged(
        object? sender, System.Collections.Specialized.NotifyCollectionChangedEventArgs e)
    {
        if (e.Action == System.Collections.Specialized.NotifyCollectionChangedAction.Reset)
        {
            foreach (var hooked in _hookedProjects)
                hooked.PropertyChanged -= OnProjectRowPropertyChanged;
            _hookedProjects.Clear();
        }
        foreach (var item in e.OldItems?.OfType<ProjectItemViewModel>() ?? Enumerable.Empty<ProjectItemViewModel>())
        {
            if (_hookedProjects.Remove(item))
                item.PropertyChanged -= OnProjectRowPropertyChanged;
        }
        foreach (var item in Projects)
        {
            if (_hookedProjects.Add(item))
                item.PropertyChanged += OnProjectRowPropertyChanged;
        }
        ScheduleRowsRebuild();
    }

    private void OnConversationRowPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        // The same properties the old ListCollectionView live-shaped on.
        if (e.PropertyName is nameof(ConversationItemViewModel.GroupKey)
            or nameof(ConversationItemViewModel.GroupRank)
            or nameof(ConversationItemViewModel.UpdatedAt)
            or nameof(ConversationItemViewModel.ProjectId)
            or nameof(ConversationItemViewModel.Title))
            ScheduleRowsRebuild();
    }

    private void OnProjectRowPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is nameof(ProjectItemViewModel.IsExpanded))
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
        // Phase 14 — the sidebar shows exactly one side of the archive line.
        // The archive view is deliberately FLAT (no project sections): it is a
        // recovery surface, and an archived chat's project no longer says
        // anything useful about where to find it.
        var visible = Conversations.Where(c => c.IsArchived == IsArchiveView).ToList();
        var rows = SidebarRowsBuilder.Build(
            IsArchiveView ? new List<ProjectItemViewModel>() : Projects.ToList(),
            visible,
            MatchesSearch,
            IsArchiveView ? new List<MessageSearchHitViewModel>() : MessageSearchResults.ToList(),
            showSearchSection: !IsArchiveView && (IsSearchingMessages || HasMessageSearchResults));

        // In-place reconcile (FIX 12 Part B). The old Clear()+Add-all forced
        // WPF to drop and rebind every row container on every refresh — the
        // rebind churn behind the wrong-type CanExecute requeries (and visible
        // flicker). Most rebuilds produce an element-equal list (conversation/
        // project/hit rows are stable VM references; header and project-member
        // rows are value-equal records), so usually this touches nothing.
        var shared = Math.Min(SidebarRows.Count, rows.Count);
        for (var i = 0; i < shared; i++)
        {
            if (!Equals(SidebarRows[i], rows[i]))
                SidebarRows[i] = rows[i];
        }
        for (var i = SidebarRows.Count - 1; i >= rows.Count; i--)
            SidebarRows.RemoveAt(i);
        for (var i = shared; i < rows.Count; i++)
            SidebarRows.Add(rows[i]);

        // The skeleton is gated on the row count, and the in-place reconcile
        // above can change it without a single collection event the binding
        // would notice; raise it explicitly.
        OnPropertyChanged(nameof(ShowListSkeleton));
        OnPropertyChanged(nameof(ShowArchiveEmptyState));
    }

    partial void OnIsSearchingMessagesChanged(bool value) => ScheduleRowsRebuild();

    partial void OnHasMessageSearchResultsChanged(bool value) => ScheduleRowsRebuild();

    partial void OnSearchTextChanged(string value)
    {
        ConversationsView.Refresh();
        foreach (var project in Projects)
            project.RefreshFilter(); // project header counts stay live
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
    private async Task OpenMessageHitAsync(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is not MessageSearchHitViewModel hit || hit.IsPulling)
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

    // ── Projects (web parity round 3) ────────────────────────────────────────

    /// <summary>Rebuilds the project list from the server, keeping expand state.</summary>
    private async Task LoadProjectsAsync()
    {
        try
        {
            var response = await _api.ListProjectsAsync();
            var expandState = Projects.ToDictionary(f => f.Id, f => f.IsExpanded);

            Projects.Clear();
            foreach (var dto in response.Projects)
            {
                var project = new ProjectItemViewModel(dto, Conversations, MatchesSearch);
                if (expandState.TryGetValue(project.Id, out var wasExpanded))
                    project.IsExpanded = wasExpanded;
                Projects.Add(project);
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Could not load projects");
        }

        // Main-list filter depends on which projects exist.
        ConversationsView.Refresh();
    }

    [RelayCommand]
    private async Task NewProjectAsync()
    {
        try
        {
            var dto = await _api.CreateProjectAsync(
                Localization.LocaleService.Instance.T("project.defaultName"));
            var project = new ProjectItemViewModel(dto, Conversations, MatchesSearch);
            Projects.Add(project);

            // Web parity: the fresh project appears with its name in inline edit mode.
            project.EditingName = project.Name;
            project.IsRenaming = true;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to create project");
            StatusMessage = "Could not create project - server unreachable";
        }
    }

    /// <summary>Raised when the user asks to manage a project (Phase 12).
    /// The shell owns the dialog; this VM owns no windows.</summary>
    public event EventHandler<ProjectItemViewModel>? ProjectSettingsRequested;

    /// <summary>Context-menu "Project settings": shared instructions + KB,
    /// server-backed so the change shows up on web too.</summary>
    [RelayCommand]
    private void OpenProjectSettings(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is ProjectItemViewModel project)
            ProjectSettingsRequested?.Invoke(this, project);
    }

    /// <summary>Re-reads a single project after the settings dialog saved, so
    /// the sidebar name stays in step without a full reload.</summary>
    public async Task RefreshProjectsAfterSettingsAsync()
    {
        await LoadProjectsAsync();
    }

    /// <summary>Context-menu Rename project: inline editor in the header row.</summary>
    [RelayCommand]
    private void RenameProject(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is not ProjectItemViewModel project)
            return;
        project.EditingName = project.Name;
        project.IsRenaming = true;
    }

    /// <summary>Inline project-rename confirm (Enter / focus lost). Empty or unchanged = cancel.</summary>
    public async Task CommitProjectInlineRenameAsync(ProjectItemViewModel project)
    {
        if (!project.IsRenaming)
            return;
        project.IsRenaming = false;

        var name = project.EditingName.Trim();
        if (name.Length == 0 || name == project.Name)
            return;

        try
        {
            var dto = await _api.RenameProjectAsync(project.Id, name);
            project.Name = string.IsNullOrWhiteSpace(dto.Name) ? name : dto.Name!;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to rename project {Id}", project.Id);
            StatusMessage = "Could not rename project - server unreachable";
        }
    }

    [RelayCommand]
    private async Task DeleteProjectAsync(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is not ProjectItemViewModel project)
            return;

        var confirmed = await _dialogs.ConfirmAsync(
            Localization.LocaleService.Instance.T("confirm.deleteProjectTitle"),
            Localization.LocaleService.Instance.T("confirm.deleteProject", project.Name));
        if (!confirmed)
            return;

        try
        {
            await _api.DeleteProjectAsync(project.Id);
        }
        catch (HttpRequestException ex) when (ex.StatusCode == System.Net.HttpStatusCode.NotFound)
        {
            // Already gone server-side — proceed with local removal.
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to delete project {Id}", project.Id);
            StatusMessage = "Could not delete project - server unreachable";
            return;
        }

        Projects.Remove(project);

        // The server unfiles members; mirror that locally so they reappear
        // in the time buckets and survive offline restarts.
        foreach (var conv in Conversations.Where(c => c.ProjectId == project.Id).ToList())
        {
            conv.ProjectId = null;
            await _store.UpsertConversationAsync(conv.ToEntity());
        }
        ConversationsView.Refresh();
    }

    [RelayCommand]
    private async Task MoveToProjectAsync(MoveToProjectRequest? request)
    {
        if (request is null)
            return;
        var (conv, projectId) = (request.Conversation, request.ProjectId);
        if (conv.ProjectId == projectId)
            return;

        try
        {
            await _api.MoveConversationToProjectAsync(conv.Id, projectId);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to move conversation {Id} to project", conv.Id);
            StatusMessage = "Could not move chat - server unreachable";
            return;
        }

        // Live filtering on ProjectId re-homes the row in both sections.
        conv.ProjectId = projectId;
        await _store.UpsertConversationAsync(conv.ToEntity());
    }

    /// <summary>Context-menu "New project…": create a project, then file the chat into it.</summary>
    [RelayCommand]
    private async Task MoveToNewProjectAsync(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is not ConversationItemViewModel conv)
            return;
        var name = Views.Dialogs.InputDialog.Show(
            System.Windows.Application.Current?.MainWindow,
            Localization.LocaleService.Instance.T("project.new"),
            Localization.LocaleService.Instance.T("project.namePrompt"));
        if (string.IsNullOrWhiteSpace(name))
            return;

        try
        {
            var dto = await _api.CreateProjectAsync(name.Trim());
            Projects.Add(new ProjectItemViewModel(dto, Conversations, MatchesSearch));
            await MoveToProjectAsync(new MoveToProjectRequest(conv, dto.Id));
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to create project for move");
            StatusMessage = "Could not create project - server unreachable";
        }
    }

    partial void OnSelectedConversationChanged(
        ConversationItemViewModel? oldValue, ConversationItemViewModel? newValue)
    {
        // Selection is VM-authoritative: rows live in several ItemsControls
        // (project sections + time buckets), so each row highlights itself
        // off IsSelected instead of a Selector.
        if (oldValue is not null) oldValue.IsSelected = false;
        if (newValue is not null) newValue.IsSelected = true;
        ConversationSelected?.Invoke(this, newValue);
    }

    /// <summary>Row click in any sidebar section.</summary>
    // FIX 12: every command reachable from a SidebarRows template takes
    // object? and pattern-matches. SidebarRows mixes 6 row types in ONE
    // recycled ItemsControl; while RebuildSidebarRowsNow repopulates it, WPF
    // transiently requeries a button's command with a wrong-type DataContext
    // (a group header lands on a conversation-row button) — and CommunityToolkit's
    // RelayCommand<T>.CanExecute THROWS on a non-null wrong-type parameter
    // ("Something went wrong" crash). object? + match makes that a no-op by
    // construction. Single-type lists elsewhere keep their typed commands.
    [RelayCommand]
    private void SelectConversation(object? parameter)
    {
        if (parameter is ConversationItemViewModel item)
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
        // Phase 14 — archived chats belong to the archive view only.
        if (vm.IsArchived != IsArchiveView)
            return false;
        // Hide chats filed in a project we know about (they render in that project's
        // section). Unknown project ids (offline, stale) fall back to the time buckets
        // so the chat never disappears.
        if (vm.ProjectId is not null && Projects.Any(f => f.Id == vm.ProjectId))
            return false;
        return MatchesSearch(vm);
    }

    /// <summary>
    /// Loads the sidebar. Structured so that ONE failing step cannot blank the
    /// chat history: the conversation list is the primary job and everything
    /// after it (projects, model catalog, empty-chat cleanup) is an
    /// enhancement that fails on its own.
    ///
    /// This shape is the lesson from the Phase-9 regression: a SQLite error
    /// raised while reading the local store propagated out of the single
    /// try/catch wrapping the whole method, so the user saw "Failed to load
    /// conversations" and an EMPTY sidebar even though the server sync, the
    /// projects fetch and the rest were fine.
    /// </summary>
    public async Task LoadAsync()
    {
        IsLoading = true;
        StatusMessage = null;
        try
        {
            // 1. The primary job: show the chat history. Local first for
            //    instant paint, then reconcile with the server.
            var listLoaded = await LoadConversationListAsync();

            // 2. Enhancements. Each is isolated: a failure here degrades one
            //    part of the sidebar, never the history.
            await RunIsolatedAsync(LoadProjectsAsync, "load projects");
            await RunIsolatedAsync(ResolveDefaultModelAsync, "resolve default model");
            if (listLoaded)
            {
                // Prune leftover empty "New conversation" placeholders so the sidebar
                // matches the web (which never lists never-used chats). Silent: these
                // have zero messages, so there is nothing to lose.
                await RunIsolatedAsync(CleanupEmptyChatsSilentAsync, "cleanup empty chats");
                // Phase 3 D: push local agent conversations to their server
                // mirror (fire-and-forget — sidebar must not wait on it).
                _ = Task.Run(() => _agentSync.SyncAllAsync());
            }
        }
        finally
        {
            IsLoading = false;
        }
    }

    /// <summary>Local store → server sync → local store. Returns false when
    /// the history could not be shown at all, which is the only case that
    /// deserves the hard error message.</summary>
    private async Task<bool> LoadConversationListAsync()
    {
        var shownFromLocal = false;
        try
        {
            await RepopulateFromStoreAsync();
            shownFromLocal = true;
        }
        catch (Exception ex)
        {
            // A broken local mirror must not stop the server reconcile below —
            // that is what repopulates the store and can heal this.
            _logger.LogError(ex, "Could not read the local conversation store");
        }

        try
        {
            var synced = await _syncService.SyncFromServerAsync();
            if (!synced)
            {
                StatusMessage = shownFromLocal
                    ? "Offline - showing local history"
                    : "Failed to load conversations";
                return shownFromLocal;
            }
            await RepopulateFromStoreAsync();
            return true;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to load conversations");
            // Keep whatever the local read managed to show; only claim failure
            // when the user is actually looking at an empty sidebar.
            StatusMessage = shownFromLocal
                ? "Offline - showing local history"
                : "Failed to load conversations";
            return shownFromLocal;
        }
    }

    /// <summary>Runs a sidebar enhancement, logging and swallowing any
    /// failure. Deliberately catches EVERYTHING: the caller's contract is
    /// "the chat history stays on screen no matter what this step does".</summary>
    private async Task RunIsolatedAsync(Func<Task> step, string what)
    {
        try
        {
            await step();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Sidebar step failed: {Step}", what);
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
            // "Selected" is not the same as "in use". A new chat whose
            // attachment is still being uploaded — the server OCRs a scanned
            // PDF before it answers, which was measured at minutes — has no
            // stored messages yet and stops being selected the moment the
            // user clicks another chat while waiting. Deleting it there threw
            // the reply away: the turn came back to a conversation that no
            // longer existed locally (FOREIGN KEY) or on the server (404).
            if (_activity?.IsBusy(conv.Id) == true)
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
            Application.Services.ToastService.Instance.Show(
                Application.Interfaces.ToastKind.Info, "cleanup.noneFound");
            return;
        }

        var confirmed = await _dialogs.ConfirmAsync(
            Localization.LocaleService.Instance.T("confirm.deleteEmptyTitle"),
            Localization.LocaleService.Instance.T("confirm.deleteEmpty", candidates.Count));
        if (!confirmed)
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
    private void Rename(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is not ConversationItemViewModel item)
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
    private async Task DeleteAsync(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is not ConversationItemViewModel item) return;

        var confirmed = await _dialogs.ConfirmAsync(
            Localization.LocaleService.Instance.T("confirm.deleteChatTitle"),
            Localization.LocaleService.Instance.T("confirm.deleteChat", item.DisplayTitle));
        if (!confirmed)
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

    /// <summary>
    /// Phase 14 — archive or restore a chat. Server first: unlike pin, this
    /// decides which LIST the chat belongs to, so a local flag the server
    /// never accepted would make the chat vanish here and stay put on the web.
    /// </summary>
    [RelayCommand]
    private async Task ToggleArchiveAsync(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is not ConversationItemViewModel item) return;

        var newValue = !item.IsArchived;
        try
        {
            // A local-only agent chat has no server row of its own; archive it
            // on its mirror when it has one, otherwise keep it local.
            var serverId = item.Source == ConversationSource.Server
                ? item.Id
                : (await _store.GetConversationAsync(item.Id))?.ServerId;
            if (serverId is { Length: > 0 })
                await _api.SetConversationArchivedAsync(serverId, newValue);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogError(ex, "Failed to archive conversation {Id}", item.Id);
            StatusMessage = newValue
                ? "Could not archive - server unreachable"
                : "Could not restore - server unreachable";
            return;
        }

        item.IsArchived = newValue;
        await _store.UpsertConversationAsync(item.ToEntity());

        // The row just left the list the user is looking at. Drop the
        // selection so the chat pane doesn't keep showing a chat that is no
        // longer listed (mirror of the web behaviour).
        if (SelectedConversation == item)
            SelectedConversation = null;

        ConversationsView.Refresh();
        RebuildSidebarRowsNow();
    }

    /// <summary>Open the archive view (sidebar footer / user menu).</summary>
    [RelayCommand]
    private void ShowArchive() => IsArchiveView = true;

    /// <summary>Back to the live chat list.</summary>
    [RelayCommand]
    private void ExitArchive() => IsArchiveView = false;

    [RelayCommand]
    private async Task TogglePinAsync(object? parameter)
    {
        // FIX 12: object? + match — see SelectConversation.
        if (parameter is not ConversationItemViewModel item) return;

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
        Projects.Clear();
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
        // "auto" (or empty) is the routing sentinel, not a catalog entry —
        // it is always valid and must never be "corrected" to a concrete
        // model (UX polish #3: Auto is the fresh-install default).
        if (string.IsNullOrEmpty(DefaultModelId) || DefaultModelId == "auto")
            return;

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
