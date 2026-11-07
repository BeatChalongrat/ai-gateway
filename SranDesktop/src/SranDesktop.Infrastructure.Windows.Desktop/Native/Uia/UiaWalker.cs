using System.Windows.Automation;

namespace SranDesktop.Infrastructure.Native.Uia;

/// <summary>
/// The real UI Automation walk (UIA layer). One Descendants query filtered to
/// interactive control types — far cheaper than walking the raw tree, and the
/// filter is what keeps Office/browser trees from returning thousands of
/// layout containers. Callers run this on a background thread with a timeout:
/// UIA calls into the target process and can hang on an unresponsive app.
/// </summary>
internal static class UiaWalker
{
    /// <summary>Interactive control types, with the short names the model sees.</summary>
    private static readonly (ControlType Type, string Name)[] InteractiveTypes =
    {
        (ControlType.Button, "button"),
        (ControlType.SplitButton, "button"),
        (ControlType.Edit, "edit"),
        (ControlType.ComboBox, "combobox"),
        (ControlType.CheckBox, "checkbox"),
        (ControlType.RadioButton, "radiobutton"),
        (ControlType.ListItem, "listitem"),
        (ControlType.TreeItem, "treeitem"),
        (ControlType.TabItem, "tabitem"),
        (ControlType.MenuItem, "menuitem"),
        (ControlType.Hyperlink, "link"),
        (ControlType.Document, "document"),
        (ControlType.Text, "text"),
    };

    private static readonly Condition InteractiveCondition = new OrCondition(
        InteractiveTypes
            .Select(t => (Condition)new PropertyCondition(AutomationElement.ControlTypeProperty, t.Type))
            .Distinct()
            .ToArray());

    /// <summary>Walks <paramref name="root"/> and returns up to
    /// <paramref name="cap"/> on-screen interactive elements (live handles +
    /// plain-data infos, index-aligned) plus the TOTAL match count for the
    /// truncation note.</summary>
    internal static (List<object> Elements, List<UiaElementInfo> Infos, int TotalFound) Walk(
        AutomationElement root, int cap = UiaFormatter.MaxElements)
    {
        var elements = new List<object>();
        var infos = new List<UiaElementInfo>();
        var found = root.FindAll(TreeScope.Descendants, InteractiveCondition);
        var total = 0;

        foreach (AutomationElement element in found)
        {
            try
            {
                var current = element.Current;
                if (current.IsOffscreen)
                    continue;
                var bounds = current.BoundingRectangle;
                if (bounds.IsEmpty || bounds.Width < 1 || bounds.Height < 1)
                    continue;

                var typeName = TypeName(current.ControlType);
                var name = current.Name ?? "";
                // Nameless static text is layout noise, not a target.
                if (typeName == "text" && name.Length == 0)
                    continue;

                total++;
                if (infos.Count >= cap)
                    continue; // keep counting for the "(+n more)" note

                infos.Add(new UiaElementInfo(
                    typeName, name, current.IsEnabled,
                    (int)bounds.X, (int)bounds.Y, (int)bounds.Width, (int)bounds.Height,
                    TryGetValue(element), TryGetToggle(element)));
                elements.Add(element);
            }
            catch (ElementNotAvailableException)
            {
                // The UI changed under the walk — skip the vanished element.
            }
        }
        return (elements, infos, total);
    }

    private static string TypeName(ControlType type)
    {
        foreach (var (candidate, name) in InteractiveTypes)
        {
            if (candidate == type)
                return name;
        }
        return type.ProgrammaticName.Replace("ControlType.", "").ToLowerInvariant();
    }

    private static string? TryGetValue(AutomationElement element)
    {
        try
        {
            return element.TryGetCurrentPattern(ValuePattern.Pattern, out var pattern)
                ? ((ValuePattern)pattern).Current.Value
                : null;
        }
        catch (ElementNotAvailableException)
        {
            return null;
        }
    }

    private static bool? TryGetToggle(AutomationElement element)
    {
        try
        {
            return element.TryGetCurrentPattern(TogglePattern.Pattern, out var pattern)
                ? ((TogglePattern)pattern).Current.ToggleState == ToggleState.On
                : null;
        }
        catch (ElementNotAvailableException)
        {
            return null;
        }
    }
}
