using System.Collections.Specialized;
using System.IO;
using System.Windows;
using System.Windows.Input;
using Microsoft.Web.WebView2.Core;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.Rendering;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Views.Dialogs;

/// <summary>
/// Attachment preview window (Phase 14 Task 2).
///
/// The only interesting part is the PDF path. The document is untrusted, so
/// the WebView2 is configured strictly:
///
///   • <c>IsScriptEnabled = false</c> — the control that stops JavaScript
///     embedded in the PDF from running;
///   • no web-message channel, no dev tools, no context menus, no autofill;
///   • the bytes are served from a private virtual host mapped read-only to a
///     temp folder, and navigation is locked to the single file we wrote
///     (<see cref="PdfPreviewSandbox"/>) with no external-browser escape.
///
/// See the class remarks on PdfPreviewSandbox for why this is stricter than
/// the artifact sandbox.
/// </summary>
public partial class FilePreviewDialog : Window
{
    private readonly FilePreviewViewModel _viewModel;
    private string? _stagedFileName;
    private string? _stagedFolder;

    public static readonly RoutedUICommand CloseCommand =
        new("Close", nameof(CloseCommand), typeof(FilePreviewDialog));

    public FilePreviewDialog(FilePreviewViewModel viewModel)
    {
        InitializeComponent();
        _viewModel = viewModel;
        DataContext = viewModel;
        viewModel.PdfReady += OnPdfReady;
        // The grid's columns come from the file, so they are rebuilt whenever
        // the sheet on screen changes (first load, a tab switch, a page).
        viewModel.SheetColumns.CollectionChanged += OnSheetColumnsChanged;
        CommandBindings.Add(new CommandBinding(CloseCommand, (_, _) => Close()));
        Loaded += async (_, _) => await viewModel.LoadAsync();
        Closed += (_, _) =>
        {
            viewModel.PdfReady -= OnPdfReady;
            viewModel.SheetColumns.CollectionChanged -= OnSheetColumnsChanged;
            CleanupStagedFile();
        };
    }

    private void OnSheetColumnsChanged(object? sender, NotifyCollectionChangedEventArgs e) =>
        Dispatcher.Invoke(RebuildSheetColumns);

    /// <summary>Rebuilds the grid's columns from the sheet currently loaded —
    /// through the same factory the render test drives, so a passing test is
    /// about what the user sees. DataGrid virtualizes rows AND columns, which
    /// is what keeps a 500-row window responsive.</summary>
    private void RebuildSheetColumns()
    {
        SheetGrid.Columns.Clear();
        foreach (var column in SheetGridColumns.Build(_viewModel.SheetColumns))
            SheetGrid.Columns.Add(column);
    }

    private async void OnPdfReady(object? sender, string fileName)
    {
        _stagedFileName = fileName;
        _stagedFolder = Path.GetDirectoryName(_viewModel.PdfPath);
        await ShowPdfAsync(fileName);
    }

    private async Task ShowPdfAsync(string fileName)
    {
        try
        {
            var environment = await WebView2Environments.GetSharedAsync();
            await PdfView.EnsureCoreWebView2Async(environment);
            var core = PdfView.CoreWebView2;
            var settings = core.Settings;

            // The security posture. IsScriptEnabled=false is the requirement:
            // a PDF must never execute script inside the app.
            settings.IsScriptEnabled = false;
            settings.IsWebMessageEnabled = false;
            settings.AreDevToolsEnabled = false;
            settings.AreDefaultContextMenusEnabled = false;
            settings.IsStatusBarEnabled = false;
            settings.IsPasswordAutosaveEnabled = false;
            settings.IsGeneralAutofillEnabled = false;

            if (_stagedFolder is { Length: > 0 } folder && Directory.Exists(folder))
            {
                core.SetVirtualHostNameToFolderMapping(
                    PdfPreviewSandbox.VirtualHost, folder,
                    CoreWebView2HostResourceAccessKind.DenyCors);
            }

            core.NavigationStarting += OnNavigationStarting;
            core.NewWindowRequested += OnNewWindowRequested;

            core.Navigate(PdfPreviewSandbox.UrlFor(fileName));
        }
        catch (WebView2RuntimeNotFoundException)
        {
            // No embedded browser on this machine — fall back to the extracted
            // text so the document is still readable, rather than a blank pane.
            await FallbackToTextAsync();
        }
        catch (Exception ex) when (ex is FileNotFoundException
                                       or System.Runtime.InteropServices.COMException
                                       or InvalidOperationException)
        {
            System.Diagnostics.Debug.WriteLine($"PDF preview failed to start: {ex.Message}");
            await FallbackToTextAsync();
        }
    }

    private async Task FallbackToTextAsync()
    {
        if (_viewModel.CanShowTextInstead)
            await _viewModel.ShowTextInsteadCommand.ExecuteAsync(null);
        else
            _viewModel.StatusMessage =
                Localization.LocaleService.Instance.T("preview.failed");
    }

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        // Only the exact document we staged. No OpenExternal branch: a link in
        // an untrusted attachment must not be able to launch the real browser.
        if (!PdfPreviewSandbox.IsAllowed(e.Uri, _stagedFileName ?? string.Empty))
            e.Cancel = true;
    }

    private static void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        // Never open a window for an untrusted document, user-initiated or not.
        e.Handled = true;
    }

    private async void Download_Click(object sender, RoutedEventArgs e)
    {
        if (DownloadRequested is { } handler)
            await handler();
    }

    /// <summary>Supplied by the caller so Save-As reuses the attachment's own
    /// resolver (local-first → filecache → authed download) instead of a
    /// second implementation here.</summary>
    public Func<Task>? DownloadRequested { get; set; }

    /// <summary>The staged copy is the whole document in plaintext on disk —
    /// remove it as soon as the window closes rather than leaving it in temp.</summary>
    private void CleanupStagedFile()
    {
        var path = _viewModel.PdfPath;
        if (string.IsNullOrEmpty(path))
            return;
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Still held by the browser process — temp cleanup, not critical.
            System.Diagnostics.Debug.WriteLine($"Preview temp cleanup skipped: {ex.Message}");
        }
    }
}
