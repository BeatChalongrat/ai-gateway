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
            va