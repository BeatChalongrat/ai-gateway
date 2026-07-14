using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace SranDesktop.Presentation.Converters;

/// <summary>true → Visible, false → Collapsed. Pass "Invert" as parameter to flip.</summary>
public sealed class BoolToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var flag = value is true;
        if (string.Equals(parameter as string, "Invert", StringComparison.OrdinalIgnoreCase))
            flag = !flag;
        return flag ? Visibility.Visible : Visibility.Collapsed;
    }

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var visible = value is Visibility.Visible;
        if (string.Equals(parameter as string, "Invert", StringComparison.OrdinalIgnoreCase))
            visible = !visible;
        return visible;
    }
}
