using System.Windows;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// Attached CornerRadius consumed by the shared button templates so variants
/// (IconButton's circle vs the default rounded rectangle) reuse one template.
/// </summary>
public static class ButtonHelper
{
    public static readonly DependencyProperty CornerRadiusProperty =
        DependencyProperty.RegisterAttached(
            "CornerRadius", typeof(CornerRadius), typeof(ButtonHelper),
            new FrameworkPropertyMetadata(new CornerRadius(6)));

    public static CornerRadius GetCornerRadius(DependencyObject obj) =>
        (CornerRadius)obj.GetValue(CornerRadiusProperty);

    public static void SetCornerRadius(DependencyObject obj, CornerRadius value) =>
        obj.SetValue(CornerRadiusProperty, value);
}
