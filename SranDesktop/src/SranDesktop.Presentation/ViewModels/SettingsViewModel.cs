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
    private readonly IBrowserBridgeService _browserBridge;
    private readonly IBrowserExtensionInstaller _browserInstaller;
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

    // ── Browser bridge (Chrome extension) ──────────────────────────────────

    [ObservableProperty]
    private bool _isBrowserConnected;

    [ObservableProperty]
    private string _browserStatusText = "";

    /// <summary>Pairing token the user pastes into the extension options page.</summary>
    [ObservableProperty]
    private string _browserPairingToken = "";

    /// <summary>Dev/load-unpacked extension id added to allowed_origins.</summary>
    [ObservableProperty]
    private string _browserExtensionId = "";

    /// <summary>Post-install instructions (extension folder path); null = hidden.</summary>
    [ObservableProperty]
    private string? _browserInstallMessage;

    public event EventHandler? Saved;

    public SettingsViewModel(
        ISettingsService settings,
        IChatEdgeApiClient api,
        IStartupManager startupManager,
        ConversationListViewModel conversations,
        IBrowserBridgeService browserBridge,
        IBrowserExtensionInstaller browserInstaller,
        ILogger<SettingsViewModel> logger)
    {
        _settings = settings;
        _api = api;
        _startupManager = startupManager;
        _conversations = conversations;
        _browserBridge = browserBridge;
        _browserInstaller = browserInstaller;
        _logger = logger;
        // Live status dot while the dialog is open (same singleton-event
        // pattern as ConnectorItemViewModel; the VM is transient per dialog).
        browserBridge.StatusChanged += (_, _) =>
            System.Windows.Application.Current?.Dispatcher.Invoke(RefreshBrowserStatus);
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
        BrowserExtensionId = current.BrowserExtensionId;
        BrowserInstallMessage = null;
        try
        {
            BrowserPairingToken = _browserBridge.GetOrCreatePairingToken();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not load browser pairing token");
        }
        RefreshBrowserStatus();
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
        updated.BrowserExtensionId = BrowserExtensionId.Trim();
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

    // ── Browser bridge (Chrome extension) ──────────────────────────────────

    private void RefreshBrowserStatus()
    {
        IsBrowserConnected = _browserBridge.IsConnected;
        BrowserStatusText = IsBrowserConnected
            ? Localization.LocaleService.Instance.T("settings.browser.connected")
            : _browserBridge.LastSeenAt is { } seen
                ? Localization.LocaleService.Instance.T(
                    "settings.browser.lastSeen", seen.LocalDateTime.ToString("g"))
                : Localization.LocaleService.Instance.T("settings.browser.never");
    }

    /// <summary>Registers the native-messaging host, then shows load-unpacked
    /// instructions with the shipped extension folder.</summary>
    [RelayCommand]
    private void InstallBrowserExtension()
    {
        try
        {
            var folder = _browserInstaller.Install(
                string.IsNullOrWhiteSpace(BrowserExtensionId) ? null : BrowserExtensionId);
            BrowserInstallMessage = Localization.LocaleService.Instance.T(
                "settings.browser.installDone", folder);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Browser extension install failed");
            BrowserInstallMessage = ex.Message;
        }
    }

    [RelayCommand]
    private void CopyBrowserToken()
    {
        try
        {
            System.Windows.Clipboard.SetText(BrowserPairingToken);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Clipboard copy failed");
        }
    }
}
