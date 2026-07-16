namespace SranDesktop.Presentation.Services;

/// <summary>
/// Pure theme-setting resolution (Phase C PART 5). Maps the user's setting —
/// "Dark", "Light", or "System" — to the concrete dictionary name, consulting
/// the OS light/dark preference for "System". Kept free of WPF/registry so it is
/// unit-testable; the OS read is injected as a bool.
/// </summary>
public static class ThemeResolver
{
    public const string Dark = "Dark";
    public const string Light = "Light";
    public const string System = "System";

    public static bool IsSystem(string? setting) =>
        string.Equals(setting, System, StringComparison.OrdinalIgnoreCase)
        || string.Equals(setting, "Auto", StringComparison.OrdinalIgnoreCase);

    /// <summary>Concrete theme ("Dark"/"Light") for a setting, given the current
    /// OS dark-mode state (used only when the setting is System/Auto).</summary>
    public static string Resolve(string? setting, bool osIsDark)
    {
        if (IsSystem(setting))
            return osIsDark ? Dark : Light;
        return string.Equals(setting, Light, StringComparison.OrdinalIgnoreCase) ? Light : Dark;
    }
}
