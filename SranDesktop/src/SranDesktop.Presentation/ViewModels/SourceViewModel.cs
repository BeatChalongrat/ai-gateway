using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

namespace SranDesktop.Presentation.ViewModels;

/// <summary>A web-search citation card under an assistant message (R9 C5).</summary>
public partial class SourceViewModel : ObservableObject
{
    public string Title { get; }
    public string Url { get; }
    public string? Snippet { get; }
    public string Domain { get; }

    public SourceViewModel(string title, string url, string? snippet, string? domain)
    {
        Title = string.IsNullOrWhiteSpace(title) ? url : title;
        Url = url;
        Snippet = snippet;
        Domain = string.IsNullOrWhiteSpace(domain)
            ? (Uri.TryCreate(url, UriKind.Absolute, out var uri) ? uri.Host : url)
            : domain!;
    }

    [RelayCommand]
    private void Open()
    {
        try
        {
            System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(Url) { UseShellExecute = true });
        }
        catch
        {
            // No default browser — nothing sensible to do.
        }
    }
}
