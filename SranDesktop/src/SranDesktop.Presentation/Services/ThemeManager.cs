using System.Windows;
using Microsoft.Win32;

namespace SranDesktop.Presentation.Services;

/// <summary>
/// Swaps the theme ResourceDictionary (index 0 of the app's merged
/// dictionaries — App.xaml merges the theme first, then Styles.xaml).
/// All visuals bind colors with DynamicResource, so the switch is live.
///
/// Phase C PART 5: supports a "System" setting that follows the OS light/dark
/// preference and re-applies live when the user changes it in Windows.
/// </summary>
public static class ThemeManager
{
    /// <summary>True while the Dark dictionary is active (app default). Drives
    /// the native dark caption on dialog windows (R8b).</summary>
    public static bool IsDark { get; private set; } = true;

    /// <summary>Raised after a theme switch so open dialogs can re-skin.</summary>
    public static event EventHandler? ThemeChanged;

    /// <summary>The user's SETTING ("Dark"/"Light"/"System") — kept so an OS
    /// theme change can be re-resolved while in System mode.</summary>
    private static string _setting = ThemeResolver.Dark;
    private static bool _osHookInstalled;

    public static void Apply(string theme)
    {
        _setting = theme;
        EnsureOsHook();
        ApplyResolved();
    }

    /// <summary>Reads the Windows apps light/dark preference
    /// (AppsUseLightTheme: 0 = dark). Defaults to light if unreadable.</summary>
    public static bool OsIsDark()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            if (key?.GetValue("AppsUseLightTheme") is int v)
                return v == 0;
        }
        catch (Exception)
        {
            // Registry unavailable → fall through to the OS default (light).
        }
        return false;
    }

    private static void ApplyResolved()
    {
        var name = ThemeResolver.Resolve(_setting, OsIsDark());
        var dictionary = new ResourceDictionary
        {
            Source = new Uri(
                $"pack://application:,,,/SranDesktop.Presentation;component/Resources/Themes/{name}.xaml")
        };

        var merged = System.Windows.Application.Current.Resources.MergedDictionaries;
        if (merged.Count > 0)
            merged[0] = dictionary;
        else
            merged.Add(dictionary);

        IsDark = name == ThemeResolver.Dark;
        ThemeChanged?.Invoke(null, EventArgs.Empty);
    }

    private static void EnsureOsHook()
    {
        if (_osHookInstalled)
            return;
        _osHookInstalled = true;
        SystemEvents.UserPreferenceChanged += (_, e) =>
        {
            // Re-resolve only when following the OS AND a general pref changed.
            if (e.Category == UserPreferenceCategory.General && ThemeResolver.IsSystem(_setting))
            {
                var dispatcher = System.Windows.Application.Current?.Dispatcher;
                if (dispatcher is null || dispatcher.CheckAccess())
                    ApplyResolved();
                else
                    dispatcher.Invoke(ApplyResolved);
            }
        };
    }
}
