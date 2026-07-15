using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;
using SranDesktop.Presentation.Rendering;
using SranDesktop.Presentation.Services;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// Hosts a WebView2 that renders an <see cref="ArtifactViewModel"/> (R9 A2).
/// The environment is shared app-wide (one user-data folder); rendering
/// re-runs on source changes and theme toggles.
/// </summary>
public partial class ArtifactControl : UserControl
{
    private ArtifactViewModel? _viewModel;
    private bool _webViewReady;
    private Window? _fullscreenWindow;

    /// <summary>Set true on the instance inside the fullscreen window so it
    /// doesn't recursively spawn another fullscreen window.</summary>
    public bool IsFullscreenHost { get; set; }

    public ArtifactControl()
    {
        InitializeComponent();
        DataContextChanged += OnDataContextChanged;
    }

    private async void ArtifactControl_Loaded(object sender, RoutedEventArgs e)
    {
        ThemeManager.ThemeChanged += OnThemeChanged;
        await InitializeAsync();
        Render();
    }

    private void ArtifactControl_Unloaded(object sender, RoutedEventArgs e)
    {
        ThemeManager.ThemeChanged -= OnThemeChanged;
    }

    private async Task InitializeAsync()
    {
        if (_webViewReady)
            return;
        try
        {
            // Shared with MathBlockControl (Phase 12 C) — one browser
            // process pool for every embedded surface.
            var environment = await WebView2Environments.GetSharedAsync();
            await ArtifactWebView.EnsureCoreWebView2Async(environment);
            var core = ArtifactWebView.CoreWebView2;
            var settings = core.Settings;
            settings.IsStatusBarEnabled = false;
            settings.AreDevToolsEnabled = false;
            settings.IsWebMessageEnabled = true;
            settings.AreDefaultContextMenusEnabled = false;
            // Phase 6 Task 3: never offer to save passwords / autofill in an
            // untrusted, model-generated document.
            settings.IsPasswordAutosaveEnabled = false;
            settings.IsGeneralAutofillEnabled = false;

            // Serve the bundled JS libs from a virtual host with least
            // privilege — only this host can load app assets, and only that
            // one folder is exposed (read-only Deny for cross-origin).
            var libFolder = Path.Combine(AppContext.BaseDirectory, "Assets", "WebLibs");
            if (Directory.Exists(libFolder))
            {
                core.SetVirtualHostNameToFolderMapping(
                    ArtifactSandbox.VirtualHost, libFolder,
                    CoreWebView2HostResourceAccessKind.Allow);
            }

            // Navigation lock: only the initial content + the bundled-lib host
            // load in-frame; http(s) elsewhere opens in the system browser;
            // everything else is denied. New windows never open in-app.
            core.NavigationStarting += OnNavigationStarting;
            core.NewWindowRequested += OnNewWindowRequested;
            _webViewReady = true;
        }
        catch (WebView2RuntimeNotFoundException)
        {
            ShowFallback("WebView2 Runtime is not installed — artifact preview unavailable. " +
                         "Use \"Open in browser\" instead.");
        }
        catch (Exception ex) when (ex is FileNotFoundException
                                       or System.Runtime.InteropServices.COMException
                                       or InvalidOperationException)
        {
            ShowFallback($"Artifact renderer failed to start: {ex.Message}");
        }
    }

    private void ShowFallback(string message)
    {
        FallbackText.Text = message;
        FallbackText.Visibility = Visibility.Visible;
        ArtifactWebView.Visibility = Visibility.Collapsed;
    }

    private void OnDataContextChanged(object? sender, DependencyPropertyChangedEventArgs e)
    {
        if (_viewModel is not null)
            _viewModel.PropertyChanged -= OnViewModelPropertyChanged;
        _viewModel = e.NewValue as ArtifactViewModel;
        if (_viewModel is not null)
            _viewModel.PropertyChanged += OnViewModelPropertyChanged;
        Render();
    }

    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        switch (e.PropertyName)
        {
            case nameof(ArtifactViewModel.SourceCode):
            case nameof(ArtifactViewModel.Type):
                Render();
                break;
            case nameof(ArtifactViewModel.IsFullscreen) when !IsFullscreenHost:
                UpdateFullscreen();
                break;
        }
    }

    private void OnThemeChanged(object? sender, EventArgs e) => Render();

    // ── Navigation lock (Phase 6 Task 3) ────────────────────────────────────

    private static void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        var decision = ArtifactSandbox.Decide(e.Uri);
        if (decision == ArtifactNavigation.Allow)
            return;

        // Anything not explicitly allowed is cancelled in-frame; only a
        // USER-initiated external target escalates to the real browser.
        e.Cancel = true;
        if (ArtifactSandbox.ShouldOpenExternal(decision, e.IsUserInitiated))
            OpenInSystemBrowser(e.Uri);
        else if (decision == ArtifactNavigation.OpenExternal)
            LogSuppressed(e.Uri);
    }

    private static void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        // Model-generated code must never spawn an in-app browser window.
        e.Handled = true;
        var decision = ArtifactSandbox.Decide(e.Uri);
        if (ArtifactSandbox.ShouldOpenExternal(decision, e.IsUserInitiated))
            OpenInSystemBrowser(e.Uri);
        else if (decision == ArtifactNavigation.OpenExternal)
            LogSuppressed(e.Uri);
    }

    /// <summary>A non-user-initiated external navigation (window.open /
    /// location.href from artifact JS) was suppressed.</summary>
    private static void LogSuppressed(string? uri)
    {
        var host = Uri.TryCreate(uri, UriKind.Absolute, out var parsed) ? parsed.Host : "?";
        Debug.WriteLine($"[ArtifactControl] Suppressed non-user-initiated external navigation to {host}");
    }

    private static void OpenInSystemBrowser(string uri)
    {
        try
        {
            Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true });
        }
        catch (Exception)
        {
            // Browser launch failure is non-fatal.
        }
    }

    private void Render()
    {
        if (!_webViewReady || _viewModel is null)
            return;
        try
        {
            ArtifactWebView.NavigateToString(_viewModel.BuildFullHtml());
        }
        catch (Exception ex) when (ex is InvalidOperationException or ArgumentException)
        {
            ShowFallback($"Could not render artifact: {ex.Message}");
        }
    }

    // ── Fullscreen overlay window (R9 A6) ───────────────────────────────────

    private void UpdateFullscreen()
    {
        if (_viewModel is null)
            return;

        if (_viewModel.IsFullscreen && _fullscreenWindow is null)
        {
            var host = new ArtifactControl { IsFullscreenHost = true, DataContext = _viewModel };
            _fullscreenWindow = new Window
            {
                Title = _viewModel.Title,
                WindowState = WindowState.Maximized,
                WindowStyle = WindowStyle.None,
                Background = (System.Windows.Media.Brush)FindResource("BgPrimaryBrush"),
                Content = host,
                Owner = System.Windows.Application.Current.MainWindow
            };
            _fullscreenWindow.PreviewKeyDown += (_, args) =>
            {
                if (args.Key == System.Windows.Input.Key.Escape)
                    _fullscreenWindow?.Close();
            };
            _fullscreenWindow.Closed += (_, _) =>
            {
                _fullscreenWindow = null;
                if (_viewModel is not null)
                    _viewModel.IsFullscreen = false;
            };
            _fullscreenWindow.Show();
        }
        else if (!_viewModel.IsFullscreen && _fullscreenWindow is not null)
        {
            _fullscreenWindow.Close();
        }
    }
}
