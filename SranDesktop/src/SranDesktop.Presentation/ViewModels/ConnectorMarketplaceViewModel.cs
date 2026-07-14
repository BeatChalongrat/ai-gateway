using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>Connector marketplace (R9 Feature I; oauth2 broker flow in Phase 7).</summary>
public partial class ConnectorMarketplaceViewModel : ObservableObject
{
    private readonly IMcpConnectorService _connectors;
    private readonly ConnectorOAuthCoordinator _oauth;
    private readonly ILogger<ConnectorMarketplaceViewModel> _logger;
    private List<ConnectorItemViewModel> _all = new();

    public ObservableCollection<ConnectorItemViewModel> Connectors { get; } = new();

    [ObservableProperty] private string _searchText = string.Empty;

    public ConnectorMarketplaceViewModel(
        IMcpConnectorService connectors,
        ConnectorOAuthCoordinator oauth,
        ILogger<ConnectorMarketplaceViewModel> logger)
    {
        _connectors = connectors;
        _oauth = oauth;
        _logger = logger;
    }

    partial void OnSearchTextChanged(string value) => ApplyFilter();

    [RelayCommand]
    public async Task LoadAsync()
    {
        var available = await _connectors.ListAvailableAsync();
        _all = available
            .Select(c => new ConnectorItemViewModel(_connectors, _oauth, c))
            .ToList();
        ApplyFilter();
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

/// <summary>One connector card.</summary>
public partial class ConnectorItemViewModel : ObservableObject
{
    private readonly IMcpConnectorService _connectors;
    private readonly ConnectorOAuthCoordinator _oauth;
    private readonly McpConnector _connector;

    public string Name => _connector.Name;
    public string Description => _connector.Description;
    public string Category => _connector.Category.ToUpperInvariant();
    public string ToolsText => string.Join(" · ", _connector.Tools);
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

    /// <summary>Health dot (R10 1E): green=Connected, red=Disconnected, grey=NotConfigured.</summary>
    public System.Windows.Media.Brush StatusBrush => _connectors.GetStatus(_connector.Id) switch
    {
        ConnectorStatus.Connected => System.Windows.Media.Brushes.MediumSeaGreen,
        ConnectorStatus.Disconnected => System.Windows.Media.Brushes.IndianRed,
        _ => System.Windows.Media.Brushes.Gray
    };

    public ConnectorItemViewModel(
        IMcpConnectorService connectors, ConnectorOAuthCoordinator oauth, McpConnector connector)
    {
        _connectors = connectors;
        _oauth = oauth;
        _connector = connector;
        _isInstalled = connectors.IsInstalled(connector.Id);
        connectors.StatusChanged += (_, _) =>
            System.Windows.Application.Current?.Dispatcher.Invoke(
                () => OnPropertyChanged(nameof(StatusBrush)));
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
