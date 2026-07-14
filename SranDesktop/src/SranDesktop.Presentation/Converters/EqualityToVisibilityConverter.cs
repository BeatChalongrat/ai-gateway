using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace SranDesktop.Presentation.Converters;

/// <summary>
/// Multi-binding: Visible when both values are equal non-empty strings, else
/// Collapsed. Used for the "selected" checkmark in the model / knowledge pickers.
/// </summary>
public sealed class EqualityToVisibilityConverter : IMultiValueConverter
{
    public object Convert(object?[] values, Type targetType, object? parameter, CultureInfo culture)
    {
        if (values.Length < 2)
            return Visibility.Collapsed;
        var a = values[0]?.ToString();
        var b = values[1]?.ToString();
        return !string.IsNullOrEmpty(a) && string.Equals(a, b, StringComparison.Ordinal)
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    public object[] ConvertBack(object? value, Type[] targetTypes, object? parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
