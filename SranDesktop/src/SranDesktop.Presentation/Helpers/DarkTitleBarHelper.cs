using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using SranDesktop.Presentation.Services;

namespace SranDesktop.Presentation.Helpers;

/// <summary>
/// Enables the Windows immersive dark title bar on any WPF Window
/// (Windows 10 1809+ / Windows 11). Dialogs keep their native chrome but
/// stop flashing a white caption in dark mode (R8b).
/// </summary>
internal static class DarkTitleBarHelper
{
    [DllImport("dwmapi.dll", PreserveSig = true)]
    private static extern int DwmSetWindowAttribute(
        IntPtr hwnd, int attr, ref int attrValue, int attrSize);

    private const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20;
    // Pre-20H1 builds used attribute 19 for the same flag.
    private const int DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19;

    /// <summary>Apply dark/light caption to a window whose handle exists.</summary>
    public static void SetDarkTitleBar(Window window, bool useDarkMode)
    {
        if (window is null) return;
        try
        {
            var hwnd = new WindowInteropHelper(window).Handle;
            if (hwnd == IntPtr.Zero) return;
            var value = useDarkMode ? 1 : 0;
            if (DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ref value, sizeof(int)) != 0)
                DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY, ref value, sizeof(int));
        }
        catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException)
        {
            // Unsupported OS — native light caption is the graceful fallback.
        }
    }

    /// <summary>
    /// One-line hookup for dialog windows: applies the app's current theme to
    /// the native caption once the HWND exists, and follows live theme toggles
    /// until the window closes.
    /// </summary>
    public static void Attach(Window window)
    {
        if (window is null) return;

        void Apply(object? _, EventArgs __) => SetDarkTitleBar(window, ThemeManager.IsDark);

        window.SourceInitialized += Apply;
        ThemeManager.ThemeChanged += Apply;
        window.Closed += (_, _) => ThemeManager.ThemeChanged -= Apply;

        // Handle may already exist if Attach is called after Show.
        if (new WindowInteropHelper(window).Handle != IntPtr.Zero)
            SetDarkTitleBar(window, ThemeManager.IsDark);
    }
}
