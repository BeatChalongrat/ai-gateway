using System.Windows;

namespace SranDesktop.Presentation.Services;

/// <summary>
/// Single gate for every Phase 8 animation: honors the OS reduced-motion
/// setting (SystemParameters.ClientAreaAnimation). When motion is off, all
/// durations collapse to zero so states snap instantly — storyboards still
/// run (bindings and completed-state logic stay identical), they just take
/// no time. Durations here define the phase's feel budget (120–200 ms).
/// </summary>
public static class MotionSettings
{
    /// <summary>Test hook — forces the gate regardless of the OS setting.</summary>
    internal static bool? OverrideEnabled;

    public static bool Enabled => OverrideEnabled ?? SystemParameters.ClientAreaAnimation;

    private static Duration Ms(int milliseconds) =>
        new(TimeSpan.FromMilliseconds(Enabled ? milliseconds : 0));

    /// <summary>Message entrance: fade + 8px slide-up.</summary>
    public static Duration Entrance => Ms(140);

    /// <summary>Toast fade/slide in-out.</summary>
    public static Duration Toast => Ms(160);

    /// <summary>Small state changes (expander chevron).</summary>
    public static Duration Short => Ms(150);
}
