using System.Collections.ObjectModel;
using System.IO;
using System.Windows.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Logging;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// In-app attachment preview (Phase 14 Task 2) — web P10 parity.
///
/// Before this, clicking a file chip could only download it, so reading a
/// PDF someone sent you meant leaving the app. Now each attachment routes to
/// the viewer that fits it:
///
///   image → decoded bitmap · pdf → locked-down WebView2 ·
///   office/text → the text chat-edge already extracts · anything else → said
///   plainly, with Download.
///
/// Download stays available in every state: preview is an addition, never a
/// replacement, and the states where preview fails are exactly the ones where
/// the user most wants the file itself.
/// </summary>
public partial class FilePreviewViewModel : ObservableObject
{
    private readonly Func<Task<byte[]?>> _bytesResolver;
    private readonly Func<Task<FileTextDto>>? _textResolver;
    private readonly Func<int, int, int, Task<FilePreviewDataDto>>? _structuredResolver;
    private readonly ILogger _logger;

    public string Filename { get; }

    public AttachmentPreviewKind Kind { get; private set; }

    /// <summary>Phase 16 — the structured view this file qualifies for, or
    /// null. Non-null only means "worth asking": the server still answers 415
    /// for a type it cannot read and 503 when it has no sandbox, and either
    /// falls back to the extracted text.</summary>
    public StructuredPreviewKind? Structured { get; private set; }

    public FilePreviewViewModel(
        string filename,
        string? contentType,
        Func<Task<byte[]?>> bytesResolver,
        Func<Task<FileTextDto>>? textResolver,
        ILogger logger,
        Func<int, int, int, Task<FilePreviewDataDto>>? structuredResolver = null)
    {
        Filename = filename;
        _bytesResolver = bytesResolver;
        _textResolver = textResolver;
        _structuredResolver = structuredResolver;
        _logger = logger;
        Kind = AttachmentPreviewRouter.Resolve(contentType, filename);
        // Without a server file id there is nothing to ask for extracted text,
        // so an office document degrades to "download it" rather than to a
        // pane that can never fill in.
        if (Kind == AttachmentPreviewKind.ExtractedText && textResolver is null)
            Kind = AttachmentPreviewKind.Unsupported;
        if (Kind == AttachmentPreviewKind.ExtractedText && structuredResolver is not null)
            Structured = StructuredPreviewRouter.Resolve(contentType, filename);
    }

    [ObservableProperty]
    private bool _isLoading = true;

    /// <summary>Localized line under the title: the pending/failed explanation.
    /// Null when the content itself is on screen.</summary>
    [ObservableProperty]
    private string? _statusMessage;

    [ObservableProperty]
    private BitmapSource? _image;

    /// <summary>Absolute path of the temp copy the WebView2 renders. Null until
    /// the bytes land (or if writing it failed).</summary>
    [ObservableProperty]
    private string? _pdfPath;

    [ObservableProperty]
    private string? _text;

    public bool IsImage => Kind == AttachmentPreviewKind.Image;
    public bool IsPdf => Kind == AttachmentPreviewKind.Pdf;

    /// <summary>The flat extracted text — now shown only when the structured
    /// read did not produce anything, so a spreadsheet no longer opens as a
    /// dump of its cells.</summary>
    public bool IsText => Kind == AttachmentPreviewKind.ExtractedText && !HasStructuredContent;

    public bool IsUnsupported => Kind == AttachmentPreviewKind.Unsupported;

    // ── Structured content (Phase 16) ───────────────────────────────────

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsSheet))]
    [NotifyPropertyChangedFor(nameof(IsText))]
    [NotifyPropertyChangedFor(nameof(HasStructuredContent))]
    [NotifyPropertyChangedFor(nameof(CanLoadMore))]
    [NotifyPropertyChangedFor(nameof(HasSheetTabs))]
    [NotifyPropertyChangedFor(nameof(SheetSummary))]
    private SheetPreviewDto? _sheet;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsDocument))]
    [NotifyPropertyChangedFor(nameof(IsText))]
    [NotifyPropertyChangedFor(nameof(HasStructuredContent))]
    [NotifyPropertyChangedFor(nameof(CanLoadMore))]
    private DocumentPreviewDto? _document;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsSlides))]
    [NotifyPropertyChangedFor(nameof(IsText))]
    [NotifyPropertyChangedFor(nameof(HasStructuredContent))]
    [NotifyPropertyChangedFor(nameof(CanLoadMore))]
    private SlidesPreviewDto? _slides;

    /// <summary>True while a "load more" / sheet switch is in flight.</summary>
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanLoadMore))]
    private bool _isPaging;

    public bool IsSheet => Sheet is not null;
    public bool IsDocument => Document is not null;
    public bool IsSlides => Slides is not null;
    public bool HasStructuredContent => IsSheet || IsDocument || IsSlides;

    /// <summary>The tab strip only appears for a workbook with more than one
    /// sheet — a single-sheet file has nothing to switch to.</summary>
    public bool HasSheetTabs => (Sheet?.Names?.Count ?? 0) > 1;

    /// <summary>Grid rows, each addressable by column index.</summary>
    public ObservableCollection<PreviewRowViewModel> SheetRows { get; } = new();

    /// <summary>Column letters (A, B, … AA) — the grid's header.</summary>
    public ObservableCollection<string> SheetColumns { get; } = new();

    public ObservableCollection<SheetTabViewModel> SheetTabs { get; } = new();

    /// <summary>Word blocks, or a deck's slides — one list feeds both panes.</summary>
    public ObservableCollection<PreviewBlockViewModel> Blocks { get; } = new();

    public ObservableCollection<SlidePanelViewModel> SlidePanels { get; } = new();

    /// <summary>"Showing 200 of 12,043 rows" — the honest replacement for
    /// the old "showing the first part only".</summary>
    public string SheetSummary
    {
        get
        {
            if (Sheet is not { } sheet)
                return string.Empty;
            var line = LocaleService.Instance.T("preview.rowCount")
                .Replace("{shown}", (sheet.Rows?.Count ?? 0).ToString("N0"))
                .Replace("{total}", sheet.TotalRows.ToString("N0"));
            return sheet.ColumnsTruncated
                ? line + " · " + LocaleService.Instance.T("preview.wideSheet")
                : line;
        }
    }

    public bool CanLoadMore => !IsPaging &&
        (Sheet?.HasMore == true || Document?.HasMore == true || Slides?.HasMore == true);

    /// <summary>A PDF can always fall back to the extracted text: chat-edge
    /// extracts PDFs too (including OCR for scanned ones). Offered as a button
    /// so a document the embedded viewer cannot render is still readable.</summary>
    public bool CanShowTextInstead =>
        Kind == AttachmentPreviewKind.Pdf && _textResolver is not null;

    /// <summary>Raised when the PDF bytes are on disk and the view may navigate.</summary>
    public event EventHandler<string>? PdfReady;

    public async Task LoadAsync()
    {
        IsLoading = true;
        StatusMessage = null;
        // A retry starts clean: stale rows must not sit under a fresh attempt.
        ClearStructured();
        try
        {
            switch (Kind)
            {
                case AttachmentPreviewKind.Image:
                    await LoadImageAsync();
                    break;
                case AttachmentPreviewKind.Pdf:
                    await LoadPdfAsync();
                    break;
                case AttachmentPreviewKind.ExtractedText:
                    // Structure first (a spreadsheet should open as a grid),
                    // the flat text only when that cannot be produced.
                    if (await TryLoadStructuredAsync())
                        break;
                    await LoadTextAsync();
                    break;
                default:
                    StatusMessage = LocaleService.Instance.T("preview.unsupported");
                    break;
            }
        }
        finally
        {
            IsLoading = false;
        }
    }

    private async Task LoadImageAsync()
    {
        var bytes = await SafeBytesAsync();
        if (bytes is null)
            return;
        Image = TryDecode(bytes);
        if (Image is null)
            StatusMessage = LocaleService.Instance.T("preview.failed");
    }

    private async Task LoadPdfAsync()
    {
        var bytes = await SafeBytesAsync();
        if (bytes is null)
            return;
        try
        {
            // The server's filename never reaches the filesystem — only its
            // extension, and even that is fixed to .pdf (see the sandbox).
            var folder = PreviewTempFolder();
            Directory.CreateDirectory(folder);
            var name = Rendering.PdfPreviewSandbox.TempFileName(Guid.NewGuid());
            var path = Path.Combine(folder, name);
            await File.WriteAllBytesAsync(path, bytes);
            PdfPath = path;
            PdfReady?.Invoke(this, name);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger.LogWarning(ex, "Could not stage the PDF for preview");
            StatusMessage = LocaleService.Instance.T("preview.failed");
        }
    }

    private async Task LoadTextAsync()
    {
        if (_textResolver is null)
        {
            StatusMessage = LocaleService.Instance.T("preview.unsupported");
            return;
        }
        try
        {
            var dto = await _textResolver();
            switch (ExtractedTextStatus.Resolve(dto.ExtractionStatus, dto.Text))
            {
                case ExtractedTextState.Ready:
                    Text = dto.Text;
                    // A truncated extraction is still useful — say so instead
                    // of letting the user think the document just ends there.
                    if (dto.Truncated)
                        StatusMessage = LocaleService.Instance.T("preview.truncated");
                    break;
                case ExtractedTextState.Pending:
                    StatusMessage = LocaleService.Instance.T("preview.extractPending");
                    break;
                case ExtractedTextState.Empty:
                    StatusMessage = LocaleService.Instance.T("preview.noText");
                    break;
                default:
                    StatusMessage = LocaleService.Instance.T("preview.extractFailed");
                    break;
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Could not fetch extracted text for preview");
            StatusMessage = LocaleService.Instance.T("preview.failed");
        }
    }

    // ── Structured preview (Phase 16) ───────────────────────────────────

    /// <summary>Asks for the structured view. Returns false — quietly — for
    /// every reason it might not arrive (415 for a type the server does not
    /// read, 503 when the deployment has no sandbox, offline, anything), so
    /// the caller falls back to the text every version before this one
    /// showed.</summary>
    private async Task<bool> TryLoadStructuredAsync()
    {
        if (Structured is not { } kind || _structuredResolver is null)
            return false;
        try
        {
            var limit = PageSizeFor(kind);
            var data = await _structuredResolver(0, 0, limit);
            return Adopt(data);
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            _logger.LogInformation(ex,
                "Structured preview unavailable for {File}; falling back to text", Filename);
            return false;
        }
    }

    private static int PageSizeFor(StructuredPreviewKind kind) => kind switch
    {
        StructuredPreviewKind.Sheet => PreviewPageSize.Rows,
        StructuredPreviewKind.Document => PreviewPageSize.Blocks,
        _ => PreviewPageSize.Slides,
    };

    /// <summary>Takes a payload and rebuilds the panes. Returns false when the
    /// response carried nothing renderable, which keeps the fallback honest
    /// rather than showing an empty grid.</summary>
    private bool Adopt(FilePreviewDataDto? data)
    {
        if (data?.Sheet is { } sheet)
        {
            Sheet = sheet;
            RebuildSheet(sheet);
            return true;
        }
        if (data?.Document is { } document)
        {
            Document = document;
            RebuildBlocks(document.Blocks);
            return true;
        }
        if (data?.Slides is { } slides)
        {
            Slides = slides;
            RebuildSlides(slides);
            return true;
        }
        return false;
    }

    private void RebuildSheet(SheetPreviewDto sheet)
    {
        SheetColumns.Clear();
        foreach (var column in sheet.Columns ?? new List<string>())
            SheetColumns.Add(column);

        SheetRows.Clear();
        var rows = sheet.Rows ?? new List<List<string>>();
        for (var i = 0; i < rows.Count; i++)
        {
            SheetRows.Add(new PreviewRowViewModel(
                PreviewWindowMerge.RowLabel(sheet.Offset, i), rows[i]));
        }

        SheetTabs.Clear();
        var names = sheet.Names ?? new List<string>();
        for (var i = 0; i < names.Count; i++)
            SheetTabs.Add(new SheetTabViewModel(i, names[i], i == sheet.Index));

        OnPropertyChanged(nameof(SheetSummary));
    }

    private void RebuildBlocks(List<PreviewBlockDto>? blocks)
    {
        Blocks.Clear();
        foreach (var block in blocks ?? new List<PreviewBlockDto>())
            Blocks.Add(new PreviewBlockViewModel(block));
    }

    private void RebuildSlides(SlidesPreviewDto slides)
    {
        SlidePanels.Clear();
        foreach (var slide in slides.Slides ?? new List<SlidePreviewDto>())
            SlidePanels.Add(new SlidePanelViewModel(slide, slides.TotalSlides));
    }

    /// <summary>Switch to another sheet of the same workbook.
    ///
    /// Takes <c>object?</c> and matches, per the FIX 12 rule: a command bound
    /// from a templated row must never assume the parameter's type, because
    /// RelayCommand&lt;T&gt;.CanExecute THROWS on a mismatch rather than
    /// returning false.</summary>
    [RelayCommand]
    private async Task SelectSheetAsync(object? parameter)
    {
        var index = parameter switch
        {
            SheetTabViewModel tab => tab.Index,
            int i => i,
            _ => -1,
        };
        if (index < 0 || _structuredResolver is null || Sheet is null || index == Sheet.Index)
            return;

        IsPaging = true;
        try
        {
            var data = await _structuredResolver(index, 0, PreviewPageSize.Rows);
            if (data?.Sheet is { } sheet)
            {
                Sheet = sheet;
                RebuildSheet(sheet);
            }
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            // Keep the sheet already on screen; the tab simply doesn't move.
            _logger.LogWarning(ex, "Could not switch to sheet {Index}", index);
        }
        finally
        {
            IsPaging = false;
        }
    }

    /// <summary>Fetch the next window and APPEND it. The accumulated view keeps
    /// its original offset so row numbers stay true across several pages.</summary>
    [RelayCommand]
    private async Task LoadMoreAsync()
    {
        if (_structuredResolver is null || IsPaging)
            return;

        IsPaging = true;
        try
        {
            if (Sheet is { } sheet)
            {
                var next = await _structuredResolver(
                    sheet.Index,
                    PreviewWindowMerge.NextOffset(sheet.Offset, sheet.Rows?.Count ?? 0),
                    PreviewPageSize.Rows);
                if (next?.Sheet is { } page)
                {
                    Sheet = PreviewWindowMerge.Append(sheet, page);
                    RebuildSheet(Sheet);
                }
            }
            else if (Document is { } document)
            {
                var next = await _structuredResolver(
                    0,
                    PreviewWindowMerge.NextOffset(document.Offset, document.Blocks?.Count ?? 0),
                    PreviewPageSize.Blocks);
                if (next?.Document is { } page)
                {
                    Document = PreviewWindowMerge.Append(document, page);
                    RebuildBlocks(Document.Blocks);
                }
            }
            else if (Slides is { } slides)
            {
                var next = await _structuredResolver(
                    0,
                    PreviewWindowMerge.NextOffset(slides.Offset, slides.Slides?.Count ?? 0),
                    PreviewPageSize.Slides);
                if (next?.Slides is { } page)
                {
                    Slides = PreviewWindowMerge.Append(slides, page);
                    RebuildSlides(Slides);
                }
            }
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            // Leave what is on screen; the button can be pressed again.
            _logger.LogWarning(ex, "Could not load more of {File}", Filename);
        }
        finally
        {
            IsPaging = false;
        }
    }

    private async Task<byte[]?> SafeBytesAsync()
    {
        try
        {
            var bytes = await _bytesResolver();
            if (bytes is { Length: > 0 })
                return bytes;
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            _logger.LogWarning(ex, "Could not fetch attachment bytes for preview");
        }
        StatusMessage = LocaleService.Instance.T("preview.failed");
        return null;
    }

    /// <summary>Switch a PDF to its extracted text (the embedded viewer may be
    /// unavailable, or the user may simply want selectable text).</summary>
    [RelayCommand]
    private async Task ShowTextInsteadAsync()
    {
        if (_textResolver is null)
            return;
        Kind = AttachmentPreviewKind.ExtractedText;
        NotifyKindChanged();
        await LoadAsync();
    }

    /// <summary>Retry a pending extraction — chat-edge extracts asynchronously,
    /// so "not yet" is usually resolved by asking again a moment later.</summary>
    [RelayCommand]
    private Task Retry() => LoadAsync();

    private void NotifyKindChanged()
    {
        OnPropertyChanged(nameof(IsImage));
        OnPropertyChanged(nameof(IsPdf));
        OnPropertyChanged(nameof(IsText));
        OnPropertyChanged(nameof(IsUnsupported));
        OnPropertyChanged(nameof(CanShowTextInstead));
    }

    /// <summary>Retry after a failed load must not leave the previous
    /// structured content on screen underneath the new attempt.</summary>
    private void ClearStructured()
    {
        Sheet = null;
        Document = null;
        Slides = null;
        SheetRows.Clear();
        SheetColumns.Clear();
        SheetTabs.Clear();
        Blocks.Clear();
        SlidePanels.Clear();
    }

    /// <summary>Private per-user folder for staged previews.</summary>
    internal static string PreviewTempFolder() => Path.Combine(
        Path.GetTempPath(), "SranDesktop", "preview");

    private static BitmapSource? TryDecode(byte[] bytes)
    {
        try
        {
            var image = new BitmapImage();
            using var stream = new MemoryStream(bytes);
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = stream;
            image.EndInit();
            image.Freeze();
            return image;
        }
        catch (Exception ex) when (ex is NotSupportedException or IOException or ArgumentException)
        {
            return null;
        }
    }
}
