using System.Net.Http;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Application.Interfaces;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// Drives the first-run onboarding dialog (reworked in R11 A2): Cloud vs
/// self-hosted presets, single-URL mode (nginx path routing derives both
/// endpoints), and a connection test that gates Continue.
/// </summary>
public partial class FirstRunViewModel : ObservableObject
{
    private const string CloudBaseUrl = "https://sran.ai";

    private readonly ISettingsService _settings;
    private readonly IStartupManager _startupManager;
    private readonly string _originalChatEdgeUrl;
    private readonly string _originalGatewayUrl;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanContinue))]
    private bool _isCloudSelected;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanContinue))]
    private bool _isSelfHostedSelected = true;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanContinue))]
    private string _serverBaseUrl;

    [ObservableProperty] private bool _startWithWindows;

    // ── Working folder (P3-4) ───────────────────────────────────────────────
    //
    // File tools reach only granted folders; there are no implicit built-in
    // roots. Without this step a fresh install can read and write nothing, and
    // the sandbox shell lane has no tree to stage, so every one of those tools
    // fails and nothing tells the user that picking a folder is the fix. The
    // answer is onboarding, not an implicit grant over the whole home directory.

    /// <summary>Pre-filled with Documents; the user can browse elsewhere or clear
    /// it. Empty means "decide later", which is allowed — it just leaves file
    /// tools unavailable until a folder is chosen in the composer.</summary>
    [ObservableProperty] private string _workingFolder = string.Empty;

    /// <summary>Shown as a warning when the user clears the proposal, so the
    /// consequence is stated before Continue rather than discovered later.</summary>
    public bool HasNoWorkingFolder => string.IsNullOrWhiteSpace(WorkingFolder);

    partial void OnWorkingFolderChanged(string value) =>
        OnPropertyChanged(nameof(HasNoWorkingFolder));

    [RelayCommand]
    private void BrowseWorkingFolder()
    {
        var dialog = new Microsoft.Win32.OpenFolderDialog
        {
            Title = LocaleService.Instance.T("input.selectWorkingFolder"),
            InitialDirectory = WorkingFolder
        };
        if (dialog.ShowDialog() != true)
            return;
        if (SranDesktop.Application.Services.FolderGrantValidator.Validate(
                dialog.FolderName, out var canonical)
            != SranDesktop.Application.Services.FolderGrantRejection.Ok)
        {
            ConnectionStatus = LocaleService.Instance.T("settings.folderRejected");
            return;
        }
        WorkingFolder = canonical!;
    }

    [RelayCommand]
    private void ClearProposedWorkingFolder() => WorkingFolder = string.Empty;

    // ── Connection test (A2c) ───────────────────────────────────────────────

    [ObservableProperty] private bool _isTestingConnection;

    [ObservableProperty] private string? _connectionStatus;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanContinue))]
    private bool _isConnectionValid;

    /// <summary>Cloud preset is trusted; self-hosted requires a passing test.</summary>
    public bool CanContinue => IsCloudSelected
        || (IsSelfHostedSelected && (IsConnectionValid || IsLocalDevUrl(ServerBaseUrl)));

    partial void OnServerBaseUrlChanged(string value) => IsConnectionValid = false;

    public FirstRunViewModel(ISettingsService settings, IStartupManager startupManager)
    {
        _settings = settings;
        _startupManager = startupManager;
        _originalChatEdgeUrl = settings.Current.ChatEdgeBaseUrl;
        _originalGatewayUrl = settings.Current.GatewayBaseUrl;
        _serverBaseUrl = settings.Current.GatewayBaseUrl;
        _startWithWindows = startupManager.IsEnabled;
        // An existing setting wins (re-onboarding); otherwise propose Documents.
        _workingFolder = string.IsNullOrWhiteSpace(settings.Current.WorkingFolder)
            ? SranDesktop.Application.Services.FolderGrantValidator
                  .SuggestDefaultWorkingFolder() ?? string.Empty
            : settings.Current.WorkingFolder;
    }

    /// <summary>Local dev (split ports, no nginx) skips the gate so `make up`
    /// setups keep working without a reachable stack at onboarding time.</summary>
    public static bool IsLocalDevUrl(string url) =>
        url.Contains("localhost", StringComparison.OrdinalIgnoreCase)
        || url.Contains("127.0.0.1", StringComparison.Ordinal);

    [RelayCommand]
    private async Task TestConnectionAsync()
    {
        var baseUrl = ServerBaseUrl.Trim().TrimEnd('/');
        if (baseUrl.Length == 0)
            return;

        IsTestingConnection = true;
        ConnectionStatus = LocaleService.Instance.T("setup.testing");
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };

            var gatewayOk = await ProbeAsync(http, $"{baseUrl}/__gw/health");
            // chat-edge health is GET /health at its root; behind nginx the
            // path-mapped alias /api/v1/health also works (R11 B3).
            var chatOk = await ProbeAsync(http, $"{baseUrl}/api/v1/health")
                         || await ProbeAsync(http, $"{baseUrl}/health")
                         || await ProbeAsync(http, $"{_originalChatEdgeUrl.TrimEnd('/')}/health");

            if (gatewayOk && chatOk)
            {
                ConnectionStatus = LocaleService.Instance.T("setup.connected");
                IsConnectionValid = true;
            }
            else
            {
                ConnectionStatus = $"Gateway: {(gatewayOk ? "OK" : "FAIL")} · Chat: {(chatOk ? "OK" : "FAIL")}";
                IsConnectionValid = false;
            }
        }
        finally
        {
            IsTestingConnection = false;
        }
    }

    private static async Task<bool> ProbeAsync(HttpClient http, string url)
    {
        try
        {
            using var response = await http.GetAsync(url);
            return response.IsSuccessStatusCode;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
                                       or UriFormatException or InvalidOperationException)
        {
            return false;
        }
    }

    /// <summary>Persists the onboarding choices (also creates settings.json, clearing first-run).</summary>
    public void Commit()
    {
        var updated = _settings.Current.Clone();

        if (IsCloudSelected)
        {
            updated.ChatEdgeBaseUrl = CloudBaseUrl;
            updated.GatewayBaseUrl = CloudBaseUrl;
        }
        else
        {
            var baseUrl = ServerBaseUrl.Trim().TrimEnd('/');
            if (baseUrl == _originalGatewayUrl.TrimEnd('/') && IsLocalDevUrl(baseUrl))
            {
                // Untouched local dev seed → keep the split-port defaults.
                updated.ChatEdgeBaseUrl = _originalChatEdgeUrl;
                updated.GatewayBaseUrl = _originalGatewayUrl;
            }
            else
            {
                // Single URL mode (A2b): nginx routes /api/v1/* and /v1/* by path.
                updated.ChatEdgeBaseUrl = baseUrl;
                updated.GatewayBaseUrl = baseUrl;
            }
        }

        // Re-validated at commit time: the proposal was checked when it was made,
        // but the folder could have been renamed or removed since. A stale value
        // would configure zero mounts and silently reproduce the fresh-install
        // failure this step exists to prevent.
        updated.WorkingFolder =
            !string.IsNullOrWhiteSpace(WorkingFolder)
            && SranDesktop.Application.Services.FolderGrantValidator.Validate(
                   WorkingFolder, out var canonicalWorkingFolder)
               == SranDesktop.Application.Services.FolderGrantRejection.Ok
                ? canonicalWorkingFolder!
                : string.Empty;

        updated.StartWithWindows = StartWithWindows;
        try
        {
            _startupManager.Set(StartWithWindows);
        }
        catch (Exception)
        {
            // Registry write can fail under locked-down policies — non-fatal.
        }

        _settings.Save(updated);
    }
}
