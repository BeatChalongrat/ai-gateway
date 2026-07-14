using System.Windows;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// Attached property consumed by the global TextBox template: when set, the
/// template shows this text as a placeholder while the box is empty.
/// Usage: &lt;TextBox controls:WatermarkService.Text="Search…" /&gt;
/// </summary>
public static class WatermarkService
{
    public static readonly DependencyProperty TextProperty =
        DependencyProperty.RegisterAttached(
            "Text", typeof(string), typeof(WatermarkService),
            new FrameworkPropertyMetadata(string.Empty));

    public static string GetText(DependencyObject obj) => (string)obj.GetValue(TextProperty);

    public static void SetText(DependencyObject obj, string value) => obj.SetValue(TextProperty, value);
}
