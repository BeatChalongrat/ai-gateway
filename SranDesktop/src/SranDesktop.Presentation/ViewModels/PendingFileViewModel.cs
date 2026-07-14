using System.IO;
using System.Windows.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>A file attached to the composer, uploading or ready to send.</summary>
public partial class PendingFileViewModel : ObservableObject
{
    private static readonly string[] ImageExtensions =
        { ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp" };

    public string LocalPath { get; }
    public string Name { get; }

    /// <summary>True for image files — the chip shows a thumbnail (R9 D3).</summary>
    public bool IsImage { get; }

    /// <summary>Small decoded preview for image chips; null for other files.</summary>
    public BitmapSource? ThumbnailSource { get; }

    [ObservableProperty] private double _progress;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsUploading))]
    [NotifyPropertyChangedFor(nameof(IsReady))]
    [NotifyPropertyChangedFor(nameof(IsFailed))]
    private string _state = "uploading";

    [ObservableProperty] private string? _error;

    /// <summary>Server file id once the upload completes.</summary>
    public string? FileId { get; set; }

    public bool IsUploading => State == "uploading";
    public bool IsReady => State == "ready";
    public bool IsFailed => State == "error";

    public PendingFileViewModel(string localPath)
    {
        LocalPath = localPath;
        Name = Path.GetFileName(localPath);
        IsImage = ImageExtensions.Contains(
            Path.GetExtension(localPath).ToLowerInvariant());
        if (IsImage)
            ThumbnailSource = TryDecodeThumbnail(localPath);
    }

    private static BitmapSource? TryDecodeThumbnail(string path)
    {
        try
        {
            var image = new BitmapImage();
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.DecodePixelWidth = 160; // thumbnail-sized decode, not full res
            image.UriSource = new Uri(path);
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
