using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.Services;

namespace SranDesktop.Presentation.ViewModels;

public partial class MainViewModel : ObservableObject
{
    private readonly IAuthService _authService;
    private readonly ISettingsService _settings;
    private readonly IChatEdgeApiClient _api;
    private readonly IProjectStore _projects;
    private readonly ITaskSchedulerService _scheduler;
    private readonly IMcpConnectorService _connectors;
    private readonly IUpdateService _updates;
    private readonly ILogger<MainViewModel> _logger;
    private DateTimeOffset _usageFetchedAt = DateTimeOffset.MinValue;

    public LoginViewModel Login { get; }
    public ConversationListViewModel Conversations { get; }
    public ChatViewModel Chat { get; }

    [ObservableProperty]
    private bool _isAuthenticated;

    [ObservableProperty]
    private bool _isInitializing = true;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(AvatarInitial))]
    private string? _userEmail;

    /// <summary>Avatar letter for the account footer (first char of the email).</summary>
    public string AvatarInitial =>
        string.IsNullOrEmpty(UserEmail) ? "S" : char.ToUpperInvariant(UserEmail![0]).ToString();

    /// <summary>Drives the settings-popup Dark mode toggle pill.</summary>
    [ObservableProperty]
    private bool _isDarkTheme;

    // ── Usage / credits (web parity R6) ─────────────────────────────────────

    /// <summary>Rows for the usage popup's per-model breakdown.</summary>
    public System.Collections.ObjectModel.ObservableCollection<UsageByModelItem> UsageByModel { get; } = new();

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CreditsRemainingText))]
    [NotifyPropertyChangedFor(nameof(CreditsTotalText))]
    [NotifyPropertyChangedFor(nameof(UsagePercentRemaining))]
    [NotifyPropertyChangedFor(nameof(TotalCreditsUsedText))]
    private UsageResponse? _usage;

    public string CreditsRemainingText =>
        Usage is null ? "—" : Usage.Credits.Remaining.ToString("N0");

    public string CreditsTotalText =>
        Usage is null ? "" : $"of {Usage.Credits.Total:N0} credits remaining";

    public double UsagePercentRemaining =>
        Usage is null || Usage.Credits.Total <= 0
            ? 0
            : Math.Clamp(Usage.Credits.Remaining * 100.0 / Usage.Credits.Total, 0, 100);

    public string TotalCreditsUsedText =>
        Usage is null ? "—" : $"{Usage.TotalCreditsUsed:N0} credits";

    /// <summary>Fetch/refresh usage (skips the call when fetched under a minute ago).</summary>
    public async Task RefreshUsageAsync(bool force = false)
    {
        if (!force && DateTimeOffset.UtcNow - _usageFetchedAt < TimeSpan.FromSeconds(60))
            return;
        try
        {
            var usage = await _api.GetUsageAsync();
            _usageFetchedAt = DateTimeOffset.UtcNow;
            Usage = usage;
            UsageByModel.Clear();
            foreach (var row in usage.UsageByModel.OrderByDescending(r => r.CreditsUsed).Take(8))
                UsageByModel.Add(row);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not load usage");
        }
    }

    /// <summary>Settings-popup Dark mode row: switch + persist + re-theme live.</summary>
    [RelayCommand]
    private void ToggleTheme()
    {
        var updated = _settings.Current.Clone();
        updated.Theme = IsDarkTheme ? "Light" : "Dark";
        _settings.Save(updated);
        ThemeManager.Apply(updated.Theme);
        IsDarkTheme = updated.Theme == "Dark";
    }

    /// <summary>Time-of-day greeting for the empty state, e.g. "Good morning, Somchai".</summary>
    [ObservableProperty]
    private string _greeting = "Hello";

    [ObservableProperty]
    private ConversationItemViewModel? _activeConversation;

    public MainViewModel(
        IAuthService authService,
        ISettingsService settings,
        IChatEdgeApiClient api,
        IProjectStore projects,
        ITaskSchedulerService scheduler,
        IMcpConnectorService connectors,
        IUpdateService updates,
        LoginViewModel loginViewModel,
        ConversationListViewModel conversationListViewModel,
        ChatViewModel chatViewModel,
        ILogger<MainViewModel> logger)
    {
        _authService = authService;
        _settings = settings;
        _api = api;
        _projects = projects;
        _scheduler = scheduler;
        _connectors = connectors;
        _updates = updates;
        _logger = logger;
        _isDarkTheme = !string.Equals(settings.Current.Theme, "Light", StringComparison.OrdinalIgnoreCase);
        Login = loginViewModel;
        Conversations = conversationListViewModel;
        Chat = chatViewModel;

        // i18n: restore the saved locale, persist changes, refresh derived strings.
        LocaleService.Instance.Initialize(settings.Current.Locale);
        LocaleService.Instance.LocaleChanged += (_, locale) =>
        {
            var updated = _settings.Current.Clone();
            updated.Locale = locale;
            _settings.Save(updated);
            Greeting = BuildGreeting(_authService.CurrentUser?.DisplayName, UserEmail);
            Conversations.RefreshGrouping();
        };

        Login.LoginSucceeded += (_, _) => OnAuthenticated();
        Conversations.ConversationSelected += (_, item) =>
        {
            ActiveConversation = item;
            _openTask = OpenChatSafeAsync(item);
            _ = ApplyProjectContextAsync(item?.Id);
        };

        // Empty-state send: create + open a new conversation (bypassing the New-chat
        // dedup guard, since we have a message to send), then let Chat send.
        Chat.ConversationRequested = async () =>
        {
            await Conversations.CreateNewConversationAsync();
            await _openTask; // wait for the open triggered by the new selection
        };

        // New chat dedup: don't create a blank conversation when the open one is
        // already empty (or we're on the empty state) — focus the composer instead.
        Conversations.IsCurrentChatEmpty = () => Chat.Messages.Count == 0;
        Conversations.FocusComposer = () => Chat.RequestComposerFocus();
    }

    private Task _openTask = Task.CompletedTask;

    // ── Auto-update (R11 A1) ────────────────────────────────────────────────

    private UpdateCheckResult? _pendingUpdate;
    private System.Windows.Threading.DispatcherTimer? _updateTimer;

    /// <summary>Shown in the settings popup footer.</summary>
    public string AppVersion => $"v{_updates.CurrentVersion}";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasUpdate))]
    private string? _updateVersion;

    [ObservableProperty] private string? _updateNotes;
    [ObservableProperty] private bool _isUpdateMandatory;
    [ObservableProperty] private bool _isDownloadingUpdate;
    [ObservableProperty] private double _updateDownloadProgress;

    public bool HasUpdate => UpdateVersion is not null;

    /// <summary>Kick off the delayed startup check + a 6-hour recheck timer.</summary>
    public void StartUpdateChecks()
    {
        if (_updateTimer is not null)
            return;

        _ = Task.Run(async () =>
        {
            await Task.Delay(TimeSpan.FromSeconds(30));
            await CheckForUpdateSafeAsync();
        });

        _updateTimer = new System.Windows.Threading.DispatcherTimer
        {
            Interval = TimeSpan.FromHours(6)
        };
        _updateTimer.Tick += (_, _) => _ = CheckForUpdateSafeAsync();
        _updateTimer.Start();
    }

    private async Task CheckForUpdateSafeAsync()
    {
        try
        {
            var result = await _updates.CheckForUpdateAsync();
            if (result is null)
                return;
            await System.Windows.Application.Current.Dispatcher.InvokeAsync(() =>
            {
                _pendingUpdate = result;
                UpdateNotes = Truncate(result.ReleaseNotes?.Replace('\n', ' ') ?? "", 120);
                IsUpdateMandatory = result.IsMandatory;
                UpdateVersion = result.NewVersion; // last: triggers the banner
                // Phase 8: the banner can be off-screen in the tray-minimized
                // case; the toast is announced politely either way.
                Application.Services.ToastService.Instance.Show(
                    Application.Interfaces.ToastKind.Info, "toast.updateReady",
                    result.NewVersion);
            });
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Update check errored");
        }
    }

    [RelayCommand]
    private async Task InstallUpdateAsync()
    {
        if (_pendingUpdate is null || IsDownloadingUpdate)
            return;

        IsDownloadingUpdate = true;
        UpdateDownloadProgress = 0;
        try
        {
            var progress = new Progress<double>(p => UpdateDownloadProgress = p);
            var installer = await _updates.DownloadUpdateAsync(_pendingUpdate, progress);
            _updates.LaunchInstaller(installer);
            System.Windows.Application.Current.Shutdown();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Update download failed");
            UpdateNotes = $"{Localization.LocaleService.Instance.T("update.failed")}: {ex.Message}";
            IsDownloadingUpdate = false;
        }
    }

    [RelayCommand]
    private void DismissUpdate()
    {
        if (IsUpdateMandatory)
            return; // mandatory updates can't be dismissed
        UpdateVersion = null;
        _pendingUpdate = null;
    }

    private static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..max] + "...";

    // ── Projects (R9 Feature B) ─────────────────────────────────────────────

    public System.Collections.ObjectModel.ObservableCollection<Project> Projects { get; } = new();

    public async Task LoadProjectsAsync()
    {
        try
        {
            Projects.Clear();
            foreach (var project in await _projects.GetAllAsync())
                Projects.Add(project);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not load projects");
        }
    }

    [RelayCommand]
    private async Task CreateProjectAsync()
    {
        var name = Views.Dialogs.InputDialog.Show(
            System.Windows.Application.Current.MainWindow,
            Localization.LocaleService.Instance.T("project.create"),
            Localization.LocaleService.Instance.T("project.name"));
        if (string.IsNullOrWhiteSpace(name))
            return;

        var project = new Project { Name = name.Trim() };
        await _projects.UpsertAsync(project);
        Projects.Add(project);
        OpenProject(project);
    }

    [RelayCommand]
    private void OpenProject(Project? project)
    {
        if (project is null)
            return;

        var detail = new ProjectDetailViewModel(_projects, project, _logger);
        detail.StartChatRequested = async p =>
        {
            await Conversations.CreateNewConversationAsync();
            if (ActiveConversation is { } conversation)
            {
                p.ConversationIds.Add(conversation.Id);
                await _projects.UpsertAsync(p);
                await ApplyProjectContextAsync(conversation.Id);
            }
        };
        detail.ProjectDeleted += (_, _) => _ = LoadProjectsAsync();

        // Show titles of the project's conversations.
        foreach (var id in project.ConversationIds)
        {
            var item = Conversations.Conversations.FirstOrDefault(c => c.Id == id);
            if (item is not null)
                detail.Conversations.Add(item);
        }

        var view = new Views.ProjectView(detail)
        {
            Owner = System.Windows.Application.Current.MainWindow
        };
        view.ShowDialog();
        _ = LoadProjectsAsync(); // name may have changed
    }

    /// <summary>Feed the owning project's instructions/files into the chat context.</summary>
    private async Task ApplyProjectContextAsync(string? conversationId)
    {
        try
        {
            var project = conversationId is null
                ? null
                : await _projects.FindByConversationAsync(conversationId);
            Chat.SetProjectContext(project);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Project context lookup failed");
        }
    }

    private async Task OpenChatSafeAsync(ConversationItemViewModel? item)
    {
        try
        {
            await Chat.OpenConversationAsync(item);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to open conversation");
        }
    }

    /// <summary>Called once from MainWindow.Loaded: restore a stored session if there is one.</summary>
    public async Task InitializeAsync()
    {
        try
        {
            if (await _authService.TryRestoreSessionAsync())
                OnAuthenticated();
        }
        catch (Exception ex)
        {
            // Server unreachable — fall through to the login screen; the user
            // will get a clearer error when they try to sign in.
            _logger.LogWarning(ex, "Session restore failed");
        }
        finally
        {
            IsInitializing = false;
            StartUpdateChecks(); // R11 A1c: delayed startup check + 6h recheck
        }
    }

    [RelayCommand]
    private async Task LogoutAsync()
    {
        await _authService.LogoutAsync();
        Conversations.Clear();
        ActiveConversation = null;
        UserEmail = null;
        IsAuthenticated = false;
    }

    /// <summary>Prefills the composer with a suggestion, creating a chat if needed.</summary>
    [RelayCommand]
    private void Suggestion(string? prefix)
    {
        if (string.IsNullOrEmpty(prefix))
            return;

        if (ActiveConversation is null && Conversations.NewChatCommand.CanExecute(null))
            Conversations.NewChatCommand.Execute(null);

        Chat.InputText = prefix;
        Chat.RequestComposerFocus();
    }

    private void OnAuthenticated()
    {
        UserEmail = _authService.CurrentUser?.Email;
        Greeting = BuildGreeting(_authService.CurrentUser?.DisplayName, UserEmail);
        IsAuthenticated = true;

        _ = LoadConversationsSafeAsync();
        _ = RefreshUsageAsync(force: true);
        _ = LoadProjectsAsync();
        Chat.StartMemoryPolling();
        _scheduler.Start(); // scheduled tasks tick once signed in (R9 G)
        _connectors.StartHealthChecks(); // dial installed MCP servers (R10 1E)
    }

    private static string BuildGreeting(string? displayName, string? email)
    {
        var hour = DateTime.Now.Hour;
        // Web uses only Good morning/afternoon/evening (evening covers all night).
        var timeOfDay = LocaleService.Instance.T(hour switch
        {
            >= 5 and < 12 => "empty.greet.morning",
            >= 12 and < 17 => "empty.greet.afternoon",
            _ => "empty.greet.evening"
        });

        var name = FirstName(displayName, email);
        return name is null ? $"{timeOfDay}" : $"{timeOfDay}, {name}";
    }

    private static string? FirstName(string? displayName, string? email)
    {
        // Prefer a Latin display name; otherwise fall back to the email prefix
        // (web shows the English "Somchai", not the Thai display name).
        if (!string.IsNullOrWhiteSpace(displayName))
        {
            var first = displayName.Trim().Split(' ', StringSplitOptions.RemoveEmptyEntries)[0];
            if (IsLatin(first))
                return Capitalize(first);
        }
        if (!string.IsNullOrWhiteSpace(email) && email.IndexOf('@') > 0)
            return Capitalize(email[..email.IndexOf('@')]);
        return null;
    }

    private static bool IsLatin(string s) => s.All(c => c <= 0x024F);

    private static string Capitalize(string s) =>
        string.IsNullOrEmpty(s) ? s : char.ToUpperInvariant(s[0]) + s[1..];

    private async Task LoadConversationsSafeAsync()
    {
        try
        {
            await Conversations.LoadAsync();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Conversation load failed");
        }
    }
}
