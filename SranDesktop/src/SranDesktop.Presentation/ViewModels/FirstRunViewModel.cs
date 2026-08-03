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

    // ── Connection test (A2c) ───────────────────────────────────────────────

    [ObservableProperty] private bool _isTestingConnection;

    [ObservableProperty] private string? _connectionStatus;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanContinue))]
    private bool _isConnectionValid;

    /// <summary>Dev-only escape from the connection gate. In a Release build a
    /// self-hosted URL must ALWAYS pass Test Connection before Continue — a
    /// wrong URL that slips through becomes a permanent onboarding dead-end
    /// (settings.json exists → first-run never shows again).</summary>
#if DEBUG
    internal static bool AllowLocalDevBypass { get; set; } = true;
#else
    internal static bool AllowLocalDevBypass { get; set; }
#endif

    /// <summary>Cloud preset is trusted; self-hosted requires a passing test.</summary>
    public bool CanContinue => IsCloudSelected
        || (IsSelfHostedSelected && (IsConnectionValid
            || (AllowLocalDevBypass && IsLocalDevUrl(ServerBaseUrl))));

    partial void OnServerBaseUrlChanged(string value) => IsConnectionValid = false;

    public FirstRunViewModel(ISettingsService settings, IStartupManager startupManager)
    {
        _settings = settings;
        _startupManager = startupManager;
        _originalChatEdgeUrl = settings.Current.ChatEdgeBaseUrl;
        _originalGatewayUrl = settings.Current.GatewayBaseUrl;
        _serverBaseUrl = settings.Current.GatewayBaseUrl;
        _startWithWindows = startupManager.IsEnabled;
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
