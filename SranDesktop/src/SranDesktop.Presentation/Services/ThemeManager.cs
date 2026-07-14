using System.Windows;

namespace SranDesktop.Presentation.Services;

/// <summary>
/// Swaps the theme ResourceDictionary (index 0 of the app's merged
/// dictionaries — App.xaml merges the theme first, then Styles.xaml).
/// All visuals bind colors with DynamicResource, so the switch is live.
/// </summary>
public static class ThemeManager
{
    /// <summary>True while the Dark dictionary is active (app default). Drives
    /// the native dark caption on dialog windows (R8b).</summary>
    public static bool IsDark { get; private set; } = true;

    /// <summary>Raised after a theme switch so open dialogs can re-skin.</summary>
    public static event EventHandler? ThemeChanged;

    public static void Apply(string theme)
    {
        var name = string.Equals(theme, "Light", StringComparison.OrdinalIgnoreCase) ? "Light" : "Dark";
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

        IsDark = name == "Dark";
        ThemeChanged?.Invoke(null, EventArgs.Empty);
    }
}
