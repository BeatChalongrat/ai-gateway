using System.Globalization;
using System.Windows.Data;

namespace SranDesktop.Presentation.Converters;

/// <summary>Uppercases a string for display only (web section labels are UPPERCASE).</summary>
public sealed class UpperCaseConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
        => value?.ToString()?.ToUpperInvariant() ?? string.Empty;

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        => value?.ToString() ?? string.Empty;
}
