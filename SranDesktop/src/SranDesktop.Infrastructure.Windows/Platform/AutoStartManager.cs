using Microsoft.Win32;

namespace SranDesktop.Infrastructure.Platform;

/// <summary>
/// Manages the per-user "start with Windows" registry entry
/// (HKCU\…\Run). Starting via this entry passes --minimized so the app
/// boots straight to the tray.
/// </summary>
public static class AutoStartManager
{
    private const string RunKeyPath = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "SRAN Desktop";

    public static bool IsEnabled
    {
        get
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: false);
            return key?.GetValue(ValueName) is not null;
        }
    }

    public static void Enable()
    {
        var exePath = Environment.ProcessPath;
        if (string.IsNullOrEmpty(exePath))
            return;

        using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: true);
        key?.SetValue(ValueName, $"\"{exePath}\" --minimized");
    }

    public static void Disable()
    {
        using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: true);
        if (key?.GetValue(ValueName) is not null)
            key.DeleteValue(ValueName, throwOnMissingValue: false);
    }

    public static void Set(bool enabled)
    {
        if (enabled) Enable();
        else Disable();
    }
}
