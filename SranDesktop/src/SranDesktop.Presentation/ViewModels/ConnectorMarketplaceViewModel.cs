using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Connector marketplace (R9 Feature I; oauth2 broker flow in Phase 7;
/// user-added custom MCP servers in the custom-connector UI).</summary>
public partial class ConnectorMarketplaceViewModel : ObservableObject
{
    private readonly IMcpConnectorService _connectors;
    private readonly ConnectorOAuthCoordinator _oauth;
    private readonly IDialogService _dialogs;
    private readonly ILogger<ConnectorMarketplaceViewModel> _logger;
    private List<ConnectorItemViewModel> _all = new();

    public ObservableCollection<ConnectorItemViewModel> Connectors { get; } = new();

    [ObservableProperty] private string _searchText = string.Empty;

    public ConnectorMarketplaceViewModel(
        IMcpConnectorService connectors,
        ConnectorOAuthCoordinator oauth,
        IDialogService dialogs,
        ILogger<ConnectorMarketplaceViewModel> logger)
    {
        _connectors = connectors;
        _oauth = oauth;
        _dialogs = dialogs;
        _logger = logger;
    }

    partial void OnSearchTextChanged(string value) => ApplyFilter();

    [RelayCommand]
    public async Task LoadAsync()
    {
        // The user's own custom connectors lead the list; catalog follows.
        var installed = await _connectors.GetInstalledAsync();
        var available = await _connectors.ListAvailableAsync();
        _all = installed
            .Where(r => r.Custom is not null)
            .Select(r => new ConnectorItemViewModel(
                _connectors, _oauth, r.Custom!, isCustom: true, onUninstalled: RemoveItem))
            .Concat(available.Select(c => new ConnectorItemViewModel(_connectors, _oauth, c)))
            .ToList();
        ApplyFilter();
    }

    /// <summary>Open the "Add custom connector" dialog; a successful add
    /// reloads so the new card appears with its live status/tools.</summary>
    [RelayCommand]
    private async Task AddCustomAsync()
    {
        var vm = new AddCustomConnectorViewModel(_connectors, _dialogs);
        var dialog = new Views.Dialogs.AddCustomConnectorDialog(vm)
        {
            Owner = System.Windows.Application.Current.MainWindow
        };
        if (dialog.ShowDialog() == true)
            await LoadAsync();
    }

    private void RemoveItem(ConnectorItemViewModel item)
    {
        _all.Remove(item);
        Connectors.Remove(item);
    }

    private void ApplyFilter()
    {
        var query = SearchText.Trim();
        Connectors.Clear();
        foreach (var item in _all)
        {
            if (query.Length == 0
                || item.Name.Contains(query, StringComparison.OrdinalIgnoreCase)
                || item.Description.Contains(query, StringComparison.OrdinalIgnoreCase)
                || item.Category.Contains(query, StringComparison.OrdinalIgnoreCase))
                Connectors.Add(item);
        }
    }
}

/// <summary>One connector card — catalog entry or a user-added custom server.</summary>
public partial class ConnectorItemViewModel : ObservableObject
{
    private readonly IMcpConnectorService _connectors;
    private readonly ConnectorOAuthCoordinator _oauth;
    private readonly McpConnector _connector;
    private readonly Action<ConnectorItemViewModel>? _onUninstalled;

    public string Name => _connector.Name;
    public string Description => _connector.Description;
    public string Category => _connector.Category.ToUpperInvariant();

    /// <summary>Custom-connector UI: user-added server (badge + uninstall
    /// removes the card entirely — it exists in no catalog).</summary>
    public bool IsCustom { get; }

    /// <summary>Catalog cards list their declared tools; a custom definition
    /// declares none, so its card shows the LIVE cached tools/list instead
    /// (refreshed via StatusChanged as connects/health checks land).</summary>
    public string ToolsText => IsCustom
        ? string.Join(" · ", _connectors.GetCachedToolNames(_connector.Id))
        : string.Join(" · ", _connector.Tools);

    public bool IsOAuth2 => _connector.AuthType == "oauth2";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(ButtonText))]
    private bool _isInstalled;

    public ConnectorFlowState FlowState =>
        IsOAuth2 ? _oauth.GetState(_connector.Id)
                 : IsInstalled ? ConnectorFlowState.Connected : ConnectorFlowState.NotConnected;

    public string ButtonText => FlowState switch
    {
        ConnectorFlowState.Connecting => LocaleService.Instance.T("connector.connecting"),
        ConnectorFlowState.Connected => LocaleService.Instance.T("connector.connected"),
        _ => LocaleService.Instance.T("connector.install")
    };

    /// <summary>Secondary line under the card for the oauth2 flow —
    /// empty (collapsed) outside Connecting/Error.</summary>
    public string StatusText => IsOAuth2
        ? FlowState switch
        {
            ConnectorFlowState.Connecting => LocaleService.Instance.T("connector.connecting"),
            ConnectorFlowState.Error =>
                $"{LocaleService.Instance.T("connector.connectFailed")}"
                + (_oauth.GetErrorReason(_connector.Id) is { Length: > 0 } reason
                    ? $" ({reason})" : ""),
            _ => ""
        }
        : "";

    /// <summary>Health dot (R10 1E): green=Connected, red=Disconnected, grey=NotConfigured.
    /// Phase C PART 5: resolved from theme brushes so it recolors in light mode
    /// (was hardcoded Brushes.* that ignored the theme).</summary>
    public System.Windows.Media.Brush StatusBrush => _connectors.GetStatus(_connector.Id) switch
    {
        ConnectorStatus.Connected => ThemeBrush("AccentGreenBrush", System.Windows.Media.Brushes.MediumSeaGreen),
        ConnectorStatus.Disconnected => ThemeBrush("AccentRedBrush", System.Windows.Media.Brushes.IndianRed),
        _ => ThemeBrush("TextTertiaryBrush", System.Windows.Media.Brushes.Gray)
    };

    private static System.Windows.Media.Brush ThemeBrush(string key, System.Windows.Media.Brush fallback) =>
        System.Windows.Application.Current?.TryFindResource(key) as System.Windows.Media.Brush ?? fallback;

    public ConnectorItemViewModel(
        IMcpConnectorService connectors, ConnectorOAuthCoordinator oauth, McpConnector connector,
        bool isCustom = false, Action<ConnectorItemViewModel>? onUninstalled = null)
    {
        _connectors = connectors;
        _oauth = oauth;
        _connector = connector;
        IsCustom = isCustom;
        _onUninstalled = onUninstalled;
        _isInstalled = connectors.IsInstalled(connector.Id);
        connectors.StatusChanged += (_, _) =>
            System.Windows.Application.Current?.Dispatcher.Invoke(() =>
            {
                OnPropertyChanged(nameof(StatusBrush));
                if (IsCustom)
                    OnPropertyChanged(nameof(ToolsText)); // live tools/list arrived
            });
        oauth.StateChanged += (_, _) =>
            System.Windows.Application.Current?.Dispatcher.Invoke(RefreshFlowState);
    }

    internal void RefreshFlowState()
    {
        if (IsOAuth2)
            IsInstalled = _connectors.IsInstalled(_connector.Id);
        OnPropertyChanged(nameof(FlowState));
        OnPropertyChanged(nameof(ButtonText));
        OnPropertyChanged(nameof(StatusText));
    }

    [RelayCommand]
    private async Task ToggleInstallAsync()
    {
        // oauth2 → broker flow (Phase 7 C3): ticket + system browser. No
        // token ever passes through the desktop, so there is nothing to
        // paste — the old InputDialog path is gone for oauth2.
        if (IsOAuth2)
        {
            var state = FlowState;
            if (state == ConnectorFlowState.Connecting)
                return; // browser round-trip already in flight
            if (state == ConnectorFlowState.Connected || IsInstalled)
            {
                await _oauth.DisconnectAsync(_connector.Id);
                IsInstalled = false;
            }
            else
            {
                await _oauth.StartConnectAsync(_connector.Id);
            }
            RefreshFlowState();
            return;
        }

        if (IsInstalled)
        {
            await _connectors.UninstallAsync(_connector.Id);
            IsInstalled = false;
            // A custom card exists in no catalog — uninstall removes it.
            _onUninstalled?.Invoke(this);
            return;
        }

        // api_key connectors keep the existing paste flow untouched.
        string? token = null;
        if (_connector.AuthType == "api_key")
        {
            token = Views.Dialogs.InputDialog.Show(
                System.Windows.Application.Current.MainWindow,
                _connector.Name,
                $"Enter your {_connector.Name} API key:");
            if (string.IsNullOrWhiteSpace(token))
                return;
        }

        await _connectors.InstallAsync(_connector.Id, token);
        IsInstalled = true;
    }
}
