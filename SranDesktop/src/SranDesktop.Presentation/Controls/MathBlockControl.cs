using System.IO;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using SranDesktop.Presentation.Rendering;
using SranDesktop.Presentation.Services;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// Renders ONE completed display-math block ($$…$$) with the bundled KaTeX
/// (Assets/WebLibs/katex, pinned in <see cref="MathHtmlBuilder.KatexVersion"/>)
/// inside a WebView2 that reuses the artifact sandbox posture: the shared app
/// environment (<see cref="WebView2Environments"/>), the appassets.sran
/// virtual host for the offline assets, the artifact CSP, and a navigation
/// lock that allows NOTHING beyond the initial content — math has no
/// legitimate navigation, so unlike artifacts there is no open-in-browser
/// escalation. Height auto-fits via a chrome.webview.postMessage of the
/// rendered document height; theme switches re-render (like ArtifactControl).
///
/// Inline math NEVER creates one of these — a chat message can carry dozens
/// of inline formulas and a browser surface each would be far too heavy;
/// inline renders as a styled Run (MarkdownRenderer.AddInlines + MathTex).
/// </summary>
public sealed class MathBlockControl : Grid
{
    private const double InitialHeight = 48;
    private const double MaxAutoHeight = 1600;

    private readonly string _tex;
    private readonly WebView2 _webView;
    private bool _webViewReady;

    public MathBlockControl(string tex)
    {
        _tex = tex;
        _webView = new WebView2
        {
            Height = InitialHeight,
            DefaultBackgroundColor = System.Drawing.Color.Transparent
        };
        Children.Add(_webView);
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        ThemeManager.ThemeChanged += OnThemeChanged;
        await InitializeAsync();
        Render();
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        ThemeManager.ThemeChanged -= OnThemeChanged;
    }

    private void OnThemeChanged(object? sender, EventArgs e) => Render();

    private async Task InitializeAsync()
    {
        if (_webViewReady)
            return;
        try
        {
            var environment = await WebView2Environments.GetSharedAsync();
            await _webView.EnsureCoreWebView2Async(environment);
            var core = _webView.CoreWebView2;
            var settings = core.Settings;
            settings.IsStatusBarEnabled = false;
            settings.AreDevToolsEnabled = false;
            settings.IsWebMessageEnabled = true; // height auto-fit channel
            settings.AreDefaultContextMenusEnabled = false;
            settings.IsPasswordAutosaveEnabled = false;
            settings.IsGeneralAutofillEnabled = false;
            settings.IsZoomControlEnabled = false;

            // Same virtual-host mapping as artifacts: only the bundled
            // WebLibs folder is exposed (KaTeX js/css/fonts live under
            // WebLibs/katex/).
            var libFolder = Path.Combine(AppContext.BaseDirectory, "Assets", "WebLibs");
            if (Directory.Exists(libFolder))
            {
                core.SetVirtualHostNameToFolderMapping(
                    ArtifactSandbox.VirtualHost, libFolder,
                    CoreWebView2HostResourceAccessKind.Allow);
            }

            // Math never navigates: the initial NavigateToString content and
            // the bundled-lib host only; everything else is cancelled with no
            // external-browser escalation.
            core.NavigationStarting += static (_, args) =>
            {
                if (ArtifactSandbox.Decide(args.Uri) != ArtifactNavigation.Allow)
                    args.Cancel = true;
            };
            core.NewWindowRequested += static (_, args) => args.Handled = true;
            core.WebMessageReceived += OnWebMessageReceived;
            _webViewReady = true;
        }
        catch (WebView2RuntimeNotFoundException)
        {
            ShowFallback();
        }
        catch (Exception ex) when (ex is FileNotFoundException
                                       or System.Runtime.InteropServices.COMException
                                       or InvalidOperationException)
        {
            ShowFallback();
        }
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string? message = null;
        try
        {
            message = e.TryGetWebMessageAsString();
        }
        catch (ArgumentException)
        {
            // Non-string web message — ignore.
        }

        if (double.TryParse(message, System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var height)
            && height > 0)
        {
            _webView.Height = Math.Min(height, MaxAutoHeight);
        }
    }

    private void Render()
    {
        if (!_webViewReady)
            return;
        try
        {
            _webView.NavigateToString(MathHtmlBuilder.Build(_tex, ThemeManager.IsDark));
        }
        catch (Exception ex) when (ex is InvalidOperationException or ArgumentException)
        {
            ShowFallback();
        }
    }

    /// <summary>WebView2 runtime unavailable → readable TeX text instead of a
    /// silently-broken pane (same posture as ArtifactControl's fallback).</summary>
    private void ShowFallback()
    {
        Children.Clear();
        var text = new TextBlock
        {
            Text = MathTex.ToInlineText(_tex),
            TextWrapping = TextWrapping.Wrap,
            FontSize = 16,
            FontStyle = FontStyles.Italic,
            FontFamily = new System.Windows.Media.FontFamily("Cambria, Georgia, Segoe UI")
        };
        text.SetResourceReference(TextBlock.ForegroundProperty, "TextPrimaryBrush");
        Children.Add(text);
    }
}
