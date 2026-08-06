using System.ComponentModel;
using System.IO;
using System.Windows;
using System.Windows.Input;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.Rendering;
using SranDesktop.Presentation.Services;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>
/// The Canvas window (Phase 15) — runs model-authored artifacts.
///
/// SECURITY POSTURE (see <see cref="CanvasSandbox"/> for the reasoning):
///
///   • <b>No native bridge.</b> AddHostObjectToScript is NEVER called here.
///     Artifact JS has no object graph into the app.
///   • postMessage is the only channel, and every inbound message must carry
///     our marker AND the token minted for the CURRENT render — a stale
///     document cannot spoof the live one.
///   • CSP with connect-src 'none' (exfiltration lock) + scripts only from
///     the bundled canvas host. No CDN.
///   • Its OWN virtual host (canvas.sran), mapped read-only with DenyCors —
///     separate from the artifact/KaTeX host and the PDF preview host.
///   • Navigation locked to that host; no external-browser escape. New
///     windows and dev tools are refused outright.
///
/// The posture is applied ONCE at initialization and never re-applied per
/// render, so an edit cannot land in a window that was configured differently.
/// </summary>
public partial class CanvasWindow : Window
{
    private readonly CanvasViewModel _viewModel;
    private readonly CanvasDocumentLoader _loader = new();
    private DispatcherTimer? _debounce;
    private bool _webViewReady;

    /// <summary>Token for the CURRENT document. Rotated on every render.</summary>
    private string _renderToken = CanvasSandbox.NewRenderToken();

    public static readonly RoutedUICommand CloseCommand =
        new("Close", nameof(CloseCommand), typeof(CanvasWindow));

    public CanvasWindow(CanvasViewModel viewModel)
    {
        InitializeComponent();
        _viewModel = viewModel;
        DataContext = viewModel;
        CommandBindings.Add(new CommandBinding(CloseCommand, (_, _) => Close()));

        viewModel.RenderRequested += (_, _) => Dispatcher.Invoke(Render);
        viewModel.PropertyChanged += OnViewModelPropertyChanged;

        Loaded += async (_, _) =>
        {
            await InitializeWebViewAsync();
            Render();
            // History last: the preview the user came for must not wait on it.
            await viewModel.LoadVersionsAsync();
        };
        Closed += (_, _) =>
        {
            viewModel.PropertyChanged -= OnViewModelPropertyChanged;
            _debounce?.Stop();
            // The staged document is the artifact in plaintext on disk —
            // remove it with the window rather than leaving it in temp.
            _loader.Dispose();
        };
    }

    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        // Live re-render while editing: restart a short quiet period on each
        // keystroke so typing does not thrash the embedded browser.
        if (e.PropertyName != nameof(CanvasViewModel.Draft))
            return;
        _debounce ??= new DispatcherTimer { Interval = CanvasViewModel.RerenderDebounce };
        _debounce.Stop();
        _debounce.Tick -= OnDebounceTick;
        _debounce.Tick += OnDebounceTick;
        _debounce.Start();
    }

    private void OnDebounceTick(object? sender, EventArgs e)
    {
        _debounce?.Stop();
        _viewModel.CommitDraft();
    }

    private async Task InitializeWebViewAsync()
    {
        if (_webViewReady)
            return;
        try
        {
            var environment = await WebView2Environments.GetSharedAsync();
            await CanvasView.EnsureCoreWebView2Async(environment);
            var core = CanvasView.CoreWebView2;
            var settings = core.Settings;

            // Scripting is ON — that is the point of an executable canvas.
            // Everything that could turn that into reach is off.
            settings.AreDevToolsEnabled = false;
            settings.IsStatusBarEnabled = false;
            settings.AreDefaultContextMenusEnabled = false;
            settings.IsPasswordAutosaveEnabled = false;
            settings.IsGeneralAutofillEnabled = false;
            // Needed for the artifact to report ready/errors. It is the ONLY
            // channel, it is validated (marker + per-render token), and it
            // carries nothing back — no host object is ever registered.
            settings.IsWebMessageEnabled = true;

            // Canvas-only hosts, read-only, DenyCors. The libs folder is
            // shared with the artifact panel but reached through a DIFFERENT
            // host, so a canvas document cannot address another surface's
            // origin; the document gets its own host over a private staging
            // folder that holds nothing but the artifact being shown.
            var libFolder = Path.Combine(AppContext.BaseDirectory, "Assets", "WebLibs");
            if (Directory.Exists(libFolder))
            {
                core.SetVirtualHostNameToFolderMapping(
                    CanvasSandbox.VirtualHost, libFolder,
                    CoreWebView2HostResourceAccessKind.DenyCors);
            }
            core.SetVirtualHostNameToFolderMapping(
                CanvasSandbox.DocumentHost, _loader.Folder,
                CoreWebView2HostResourceAccessKind.DenyCors);

            core.NavigationStarting += OnNavigationStarting;
            core.NewWindowRequested += OnNewWindowRequested;
            core.WebMessageReceived += OnWebMessageReceived;
            _webViewReady = true;
        }
        catch (WebView2RuntimeNotFoundException)
        {
            _viewModel.ReportRuntimeError(
                Localization.LocaleService.Instance.T("canvas.noWebView2"));
        }
        catch (Exception ex) when (ex is FileNotFoundException
                                       or System.Runtime.InteropServices.COMException
                                       or InvalidOperationException)
        {
            _viewModel.ReportRuntimeError(ex.Message);
        }
    }

    private void Render()
    {
        if (!_webViewReady)
            return;
        // A fresh token per render: a message from the document being replaced
        // can never be attributed to the new one.
        _renderToken = CanvasSandbox.NewRenderToken();
        var html = CanvasHtmlBuilder.Build(
            _viewModel.Type, _viewModel.Source, _viewModel.Title, ThemeManager.IsDark, _renderToken);
        // Staged + served over the canvas document host. NavigateToString
        // would target a data: URI, which the navigation lock denies — that
        // is what made the Phase 15 canvas render an empty pane.
        _loader.Load(CanvasView.CoreWebView2, html, _renderToken);
    }

    private static void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        // Only the canvas host. No "open in the system browser" branch: this
        // is arbitrary model output, not a document the user named.
        if (!CanvasSandbox.IsNavigationAllowed(e.Uri))
            e.Cancel = true;
    }

    private static void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        // Model-generated code never spawns a window, user gesture or not.
        e.Handled = true;
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        // The bootstrap posts a STRING (JSON.stringify(...)), so read it as a
        // string and parse that — WebMessageAsJson would hand back the quoted
        // string rather than the object.
        string raw;
        try
        {
            raw = e.TryGetWebMessageAsString();
        }
        catch (ArgumentException)
        {
            return; // the document posted a non-string — not ours; ignore
        }

        // The envelope is validated (marker + current render token) before a
        // single field is trusted; an unmarked or stale message is dropped.
        if (!CanvasSandbox.TryReadMessage(raw, _renderToken, out var type, out var payload))
            return;

        if (type == "error")
            _viewModel.ReportRuntimeError(payload);
        else
            _viewModel.ReportReady();
    }
}
