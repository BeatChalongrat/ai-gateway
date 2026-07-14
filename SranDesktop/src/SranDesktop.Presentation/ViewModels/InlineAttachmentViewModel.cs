using System.IO;
using System.Windows.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using SranDesktop.Presentation.Localization;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>
/// A generated/attached media file rendered inside an assistant bubble
/// (R7 Features A/B). Images decode to a frozen BitmapSource; videos are
/// written to a temp file so MediaElement can stream from disk.
/// </summary>
public partial class InlineAttachmentViewModel : ObservableObject
{
    public string Filename { get; }
    public string ContentType { get; }
    public byte[] RawBytes { get; }

    /// <summary>Decoded bitmap for image/*; null otherwise.</summary>
    public BitmapSource? ImageSource { get; }

    /// <summary>Temp-file path for video/*; null otherwise.</summary>
    public string? VideoFilePath { get; }

    public bool IsImage => ContentType.StartsWith("image/", StringComparison.OrdinalIgnoreCase);
    public bool IsVideo => ContentType.StartsWith("video/", StringComparison.OrdinalIgnoreCase);

    /// <summary>Play/pause state — toggled by MessageBubble's overlay button.</summary>
    [ObservableProperty]
    private bool _isPlaying;

    public InlineAttachmentViewModel(string filename, string contentType, byte[] rawBytes)
    {
        Filename = filename;
        ContentType = contentType;
        RawBytes = rawBytes;

        if (IsImage)
            ImageSource = TryDecode(rawBytes);
        else if (IsVideo)
            VideoFilePath = TryWriteTempFile(filename, rawBytes);
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

    [RelayCommand]
    private void SaveAs()
    {
        var ext = Path.GetExtension(Filename);
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            FileName = Filename,
            Filter = string.IsNullOrEmpty(ext)
                ? "All files (*.*)|*.*"
                : $"{ext.TrimStart('.').ToUpperInvariant()} (*{ext})|*{ext}|All files (*.*)|*.*"
        };
        if (dialog.ShowDialog() == true)
        {
            try
            {
                File.WriteAllBytes(dialog.FileName, RawBytes);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                System.Windows.MessageBox.Show(ex.Message,
                    LocaleService.Instance.T("common.save"),
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
            }
        }
    }
}
