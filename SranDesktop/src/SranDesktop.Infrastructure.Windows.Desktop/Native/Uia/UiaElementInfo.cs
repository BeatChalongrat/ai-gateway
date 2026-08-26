using System.Text;

namespace SranDesktop.Infrastructure.Native.Uia;

/// <summary>
/// The plain-data view of one UI Automation element (UIA layer). Everything
/// the model needs to pick a target, decoupled from AutomationElement so the
/// formatting/capping logic is unit-testable without real UIA.
/// Bounds are virtual-desktop coordinates.
/// </summary>
public sealed record UiaElementInfo(
    string ControlType,
    string Name,
    bool IsEnabled,
    int X, int Y, int Width, int Height,
    string? Value = null,
    bool? Toggled = null);

/// <summary>Renders element lists in the compact indexed format the uia_*
/// tools reference by id. Capped: UIA trees can be huge and the listing goes
/// straight into model context.</summary>
public static class UiaFormatter
{
    public const int MaxElements = 400;
    private const int MaxValueChars = 80;

    public static string FormatElement(int index, UiaElementInfo e)
    {
        var sb = new StringBuilder();
        sb.Append('[').Append(index).Append("] ")
          .Append(e.ControlType).Append(" \"").Append(e.Name).Append('"')
          .Append(e.IsEnabled ? " enabled" : " disabled");
        if (e.Toggled is { } toggled)
            sb.Append(" checked=").Append(toggled ? "true" : "false");
        if (e.Value is { } value)
        {
            if (value.Length > MaxValueChars)
                value = value[..MaxValueChars] + "…";
            sb.Append(" value=\"").Append(value.Replace('\n', ' ').Replace('\r', ' ')).Append('"');
        }
        sb.Append(" bounds=(").Append(e.X).Append(',').Append(e.Y).Append(',')
          .Append(e.Width).Append(',').Append(e.Height).Append(')');
        return sb.ToString();
    }

    /// <summary>One line per element, indexed from 0 (the ids uia_click /
    /// uia_set_value take). <paramref name="totalFound"/> ≥ list count drives
    /// the truncation note when the walk found more than it kept.</summary>
    public static string BuildListing(IReadOnlyList<UiaElementInfo> elements, int totalFound, int cap = MaxElements)
    {
        var sb = new StringBuilder();
        var shown = Math.Min(elements.Count, cap);
        for (var i = 0; i < shown; i++)
            sb.AppendLine(FormatElement(i, elements[i]));
        var omitted = Math.Max(totalFound, elements.Count) - shown;
        if (omitted > 0)
            sb.AppendLine($"(+{omitted} more elements omitted — target a specific window with window_title to narrow the tree)");
        return sb.ToString().TrimEnd();
    }
}
