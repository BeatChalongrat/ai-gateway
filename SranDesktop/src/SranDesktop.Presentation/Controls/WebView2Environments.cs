using System.IO;
using Microsoft.Web.WebView2.Core;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// One app-wide WebView2 environment (single user-data folder) shared by
/// every embedded browser surface — artifacts (<see cref="ArtifactControl"/>)
/// and display math (<see cref="MathBlockControl"/>) — so all instances share
/// one browser process pool instead of each spawning their own.
/// </summary>
internal static class WebView2Environments
{
    private static Task<CoreWebView2Environment>? _sharedTask;

    public static Task<CoreWebView2Environment> GetSharedAsync()
    {
        return _sharedTask ??= CoreWebView2Environment.CreateAsync(
            userDataFolder: Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "SRAN Desktop", "WebView2"));
    }
}
