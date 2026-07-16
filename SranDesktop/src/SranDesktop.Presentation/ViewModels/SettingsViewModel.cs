using System.Collections.ObjectModel;
using System.Net.Http;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Presentation.Services;

namespace SranDesktop.Presentation.ViewModels;

public partial class SettingsViewModel : ObservableObject
{
    private readonly ISettingsService _settings;
    private readonly IChatEdgeApiClient _api;
    private readonly IStartupManager _startupManager;
    private readonly ConversationListViewModel _conversations;
    private readonly ILogger<SettingsViewModel> _logger;

    /// <summary>Delegates to the (singleton) conversation list so the button in Settings
    /// can trigger the confirm-guarded cleanup.</summary>
    public CommunityToolkit.Mvvm.Input.IAsyncRelayCommand CleanupEmptyChatsCommand =>
        _conversations.CleanupEmptyChatsCommand;

    public ObservableCollection<ModelInfo> AvailableModels { get; } = new();

    // Phase C PART 5: "System" follows the OS light/dark setting live.
    public string[] Themes { get; } = { "Dark", "Light", "System" };

    [ObservableProperty]
    private string _chatEdgeBaseUrl = string.Empty;

    [ObservableProperty]
    private string _gatewayBaseUrl = string.Empty;

    [ObservableProperty]
    private string _selectedTheme = "Dark";

    [ObservableProperty]
    private string _selectedModelId = string.Empty;

    [ObservableProperty]
    private bool _minimizeToTray = true;

    [ObservableProperty]
    private bool _startWithWindows;

    /// <summary>Opt-in crash telemetry (R11 A3c). Default OFF.</summary>
    [ObservableProperty]
    private bool _crashReportingEnabled;

    /// <summary>One-way backup of agent-mode chats to the account (Phase 3 D). Default ON.</summary>
    [ObservableProperty]
    private bool _syncAgentConversations = true;

    /// <summary>Generate Excel/Word plan files on-device when supported
    /// (Phase 12 E). Default OFF — server path stays authoritative.</summary>
    [ObservableProperty]
    private bool _preferLocalOfficeGeneration;

    /// <summary>Folders the agent's file tools may reach beyond the built-in
    /// roots (Phase 6 Task 1). Persisted on Save; consent still gates writes.</summary>
    public ObservableCollection<string> GrantedFolders { get; } = new();

    /// <summary>Localized reason shown when a picked folder can't be granted
    /// (drive/system root, non-existent, already added). Null = hidden.</summary>
    [ObservableProperty]
    private string? _folderRejectedMessage;

    [ObservableProperty]
    private bool _restartRequired;

    public event EventHandler? Saved;

    public SettingsViewModel(
        ISettingsService settings,
        IChatEdgeApiClient api,
        IStartupManager startupManager,
        ConversationListViewModel conversations,
        ILogger<SettingsViewModel> logger)
    {
        _settings = settings;
        _api = api;
        _startupManager = startupManager;
        _conversations = conversations;
        _logger = logger;
    }

    public async Task LoadAsync()
    {
        var current = _settings.Current;
        ChatEdgeBaseUrl = current.ChatEdgeBaseUrl;
        GatewayBaseUrl = current.GatewayBaseUrl;
        SelectedTheme = current.Theme;
        SelectedModelId = current.DefaultModel;
        MinimizeToTray = current.MinimizeToTray;
        CrashReportingEnabled = current.CrashReportingEnabled;
        SyncAgentConversations = current.SyncAgentConversations;
        PreferLocalOfficeGeneration = current.PreferLocalOfficeGeneration;
        GrantedFolders.Clear();
        foreach (var folder in current.GrantedFolders)
            GrantedFolders.Add(folder);
        FolderRejectedMessage = null;
        // The registry is the source of truth for auto-start.
        StartWithWindows = _startupManager.IsEnabled;
        RestartRequired = false;

        try
        {
            var response = await _api.ListModelsAsync();
            AvailableModels.Clear();
            foreach (var model in response.Models.Where(m => m.Allowed && (m.ModelKind is null or "chat")))
                AvailableModels.Add(model);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Could not load models for settings");
        }
    }

    partial void OnSelectedThemeChanged(string value) => ThemeManager.Apply(value);

    [RelayCommand]
    private void Save()
    {
        var previous = _settings.Current;
        var updated = previous.Clone();
        updated.ChatEdgeBaseUrl = ChatEdgeBaseUrl.Trim().TrimEnd('/');
        updated.GatewayBaseUrl = GatewayBaseUrl.Trim().TrimEnd('/');
        updated.Theme = SelectedTheme;
        updated.MinimizeToTray = MinimizeToTray;
        updated.StartWithWindows = StartWithWindows;
        updated.CrashReportingEnabled = CrashReportingEnabled;
        updated.SyncAgentConversations = SyncAgentConversations;
        updated.PreferLocalOfficeGeneration = PreferLocalOfficeGeneration;
        updated.GrantedFolders = GrantedFolders.ToList();
        if (!string.IsNullOrWhiteSpace(SelectedModelId))
            updated.DefaultModel = SelectedModelId;

        RestartRequired = updated.ChatEdgeBaseUrl != previous.ChatEdgeBaseUrl
                          || updated.GatewayBaseUrl != previous.GatewayBaseUrl;

        try
        {
            _startupManager.Set(StartWithWindows);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to update auto-start registry entry");
        }

        _settings.Save(updated);
        Saved?.Invoke(this, EventArgs.Empty);
    }

    /// <summary>Revert a live theme preview if the dialog is dismissed without saving.</summary>
    public void RevertTheme() => ThemeManager.Apply(_settings.Current.Theme);

    // ── Folder access (Phase 6 Task 1) ─────────────────────────────────────

    [RelayCommand]
    private void AddFolder()
    {
        FolderRejectedMessage = null;
        var dialog = new Microsoft.Win32.OpenFolderDialog { Title = "Grant folder access" };
        if (dialog.ShowDialog() != true)
            return;

        var rejection = Application.Services.FolderGrantValidator.Validate(dialog.FolderName, out var canonical);
        if (rejection != Application.Services.FolderGrantRejection.Ok || canonical is null)
        {
            FolderRejectedMessage = Localization.LocaleService.Instance.T("settings.folderRejected");
            return;
        }
        if (GrantedFolders.Any(f => string.Equals(f, canonical, StringComparison.OrdinalIgnoreCase)))
        {
            FolderRejectedMessage = Localization.LocaleService.Instance.T("settings.folderRejected");
            return;
        }
        GrantedFolders.Add(canonical);
    }

    [RelayCommand]
    private void RemoveFolder(string? folder)
    {
        if (folder is not null)
            GrantedFolders.Remove(folder);
    }
}
