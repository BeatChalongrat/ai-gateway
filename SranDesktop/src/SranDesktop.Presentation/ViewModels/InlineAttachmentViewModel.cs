using System.IO;
using System.Windows.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// A generated/attached media file rendered inside an assistant bubble
/// (R7 Features A/B). Images decode to a frozen BitmapSource; videos are
/// written to a temp file so MediaElement can stream from disk.
///
/// FIX 10b: documents (the save-chip case) are created METADATA-ONLY —
/// their bytes are fetched lazily via a resolver when the user actually
/// saves, so opening a conversation never downloads or retains megabytes
/// of PDFs/docs that may never be touched.
/// </summary>
public partial class InlineAttachmentViewModel : ObservableObject
{
    public string Filename { get; }
    public string ContentType { get; }

    /// <summary>In-hand bytes. Empty for a lazy (document) attachment until
    /// <see cref="ResolveBytesAsync"/> fetches them.</summary>
    public byte[] RawBytes => _rawBytes;

    private byte[] _rawBytes;

    /// <summary>Lazy byte source for metadata-only attachments (FIX 10b):
    /// local-first → filecache → authed download. Null for eager media.</summary>
    private readonly Func<Task<byte[]?>>? _bytesResolver;

    /// <summary>Phase 14 — fetches the text chat-edge extracted from this
    /// attachment (<c>GET /files/{id}/text</c>). Null when the attachment has
    /// no server file id (a locally generated file), which is what makes an
    /// office document fall back to "download it".</summary>
    private Func<Task<Application.Models.FileTextDto>>? _textResolver;

    /// <summary>Phase 16 — fetches the STRUCTURE of an office attachment
    /// (<c>GET /files/{id}/preview-data</c>): (sheet, offset, limit). Null for
    /// the same reason the text resolver is: no server file id, nothing to
    /// ask about.</summary>
    private Func<int, int, int, Task<Application.Models.FilePreviewDataDto>>? _structuredResolver;

    /// <summary>Supplies the extracted-text resolver after construction —
    /// only the hydration path knows the server file id.</summary>
    internal void AttachTextResolver(Func<Task<Application.Models.FileTextDto>> resolver) =>
        _textResolver = resolver;

    /// <summary>Supplies the structured-preview resolver, same reasoning.</summary>
    internal void AttachStructuredResolver(
        Func<int, int, int, Task<Application.Models.FilePreviewDataDto>> resolver) =>
        _structuredResolver = resolver;

    /// <summary>Decoded bitmap for image/*; null otherwise.</summary>
    public BitmapSource? ImageSource { get; }

    /// <summary>Temp-file path for video/*; null otherwise.</summary>
    public string? VideoFilePath { get; }

    public bool IsImage => ContentType.StartsWith("image/", StringComparison.OrdinalIgnoreCase);
    public bool IsVideo => ContentType.StartsWith("video/", StringComparison.OrdinalIgnoreCase);

    /// <summary>Everything that isn't inline-renderable media — synced PDFs,
    /// docs, spreadsheets (FIX 10). Rendered as a save-able file chip.</summary>
    public bool IsDocument => !IsImage && !IsVideo;

    /// <summary>Play/pause state — toggled by MessageBubble's overlay button.</summary>
    [ObservableProperty]
    private bool _isPlaying;

    public InlineAttachmentViewModel(string filename, string contentType, byte[] rawBytes)
    {
        Filename = filename;
        ContentType = contentType;
        _rawBytes = rawBytes;

        if (IsImage)
            ImageSource = TryDecode(rawBytes);
        else if (IsVideo)
            VideoFilePath = TryWriteTempFile(filename, rawBytes);
    }

    /// <summary>Metadata-only attachment (FIX 10b): no bytes are held until
    /// the user saves; <paramref name="bytesResolver"/> then fetches them
    /// (returning null on failure, e.g. offline). Intended for documents —
    /// media should use the eager constructor so it can render inline.</summary>
    public InlineAttachmentViewModel(string filename, string contentType, Func<Task<byte[]?>> bytesResolver)
    {
        Filename = filename;
        ContentType = contentType;
        _rawBytes = Array.Empty<byte>();
        _bytesResolver = bytesResolver;
    }

    /// <summary>Returns the attachment's bytes, fetching through the lazy
    /// resolver on first use (and keeping them for a repeat save). Null or
    /// empty means the fetch failed — callers surface that gracefully.</summary>
    internal async Task<byte[]?> ResolveBytesAsync()
    {
        if (_rawBytes.Length > 0 || _bytesResolver is null)
            return _rawBytes;
        var bytes = await _bytesResolver();
        if (bytes is { Length: > 0 })
            _rawBytes = bytes;
        return bytes;
    }

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
            image.Freeze(); // usable from any thread
            return image;
        }
        catch (Exception ex) when (ex is NotSupportedException or IOException or ArgumentException)
        {
            return null;
        }
    }

    private static string? TryWriteTempFile(string filename, byte[] bytes)
    {
        try
        {
            var ext = Path.GetExtension(filename);
            if (string.IsNullOrEmpty(ext)) ext = ".mp4";
            var path = Path.Combine(Path.GetTempPath(), "SranDesktop",
                $"video-{Guid.NewGuid():N}{ext}");
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllBytes(path, bytes);
            return path;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }

    /// <summary>
    /// Phase 14 — open the in-app preview. Every attachment opens it,
    /// including the ones that can't be rendered: the dialog then says so and
    /// offers Download, which is more useful than a click that does nothing.
    /// </summary>
    [RelayCommand]
    private void Preview()
    {
        var vm = new FilePreviewViewModel(
            Filename, ContentType,
            ResolveBytesAsync,
            _textResolver,
            Microsoft.Extensions.Logging.Abstractions.NullLogger.Instance,
            _structuredResolver);
        var dialog = new Views.Dialogs.FilePreviewDialog(vm)
        {
            Owner = System.Windows.Application.Current?.MainWindow,
            // Save-As reuses this attachment's own resolver rather than a
            // second download implementation inside the dialog.
            DownloadRequested = () => SaveAsCommand.ExecuteAsync(null),
        };
        dialog.ShowDialog();
    }

    [RelayCommand]
    private async Task SaveAsAsync()
    {
        var ext = Path.GetExtension(Filename);
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            FileName = Filename,
            Filter = string.IsNullOrEmpty(ext)
                ? "All files (*.*)|*.*"
                : $"{ext.TrimStart('.').ToUpperInvariant()} (*{ext})|*{ext}|All files (*.*)|*.*"
        };
        if (dialog.ShowDialog() != true)
            return;

        // Lazy documents fetch their bytes here, on the first save (FIX
        // 10b) — a failed fetch (offline, file deleted server-side) is a
        // toast, never a crash.
        byte[]? bytes;
        try
        {
            bytes = await ResolveBytesAsync();
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            bytes = null; // resolver failures surface as the toast below
            System.Diagnostics.Debug.WriteLine($"Attachment fetch failed: {ex.Message}");
        }
        if (bytes is null || bytes.Length == 0)
        {
            Application.Services.ToastService.Instance.Show(
                Application.Interfaces.ToastKind.Error, "attachment.downloadFailed");
            return;
        }

        try
        {
            await File.WriteAllBytesAsync(dialog.FileName, bytes);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Application.Services.ToastService.Instance.Show(
                Application.Interfaces.ToastKind.Error, "dialog.error", ex.Message);
        }
    }
}
