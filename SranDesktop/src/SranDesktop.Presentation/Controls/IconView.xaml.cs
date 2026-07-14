using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// A crisp, theme-aware vector icon: a stretched <see cref="System.Windows.Shapes.Path"/>
/// so one 24×24 Lucide geometry renders at any size with round caps. Stroke/Fill
/// accept DynamicResource theme brushes, so icons recolor on a theme switch.
/// </summary>
public partial class IconView : UserControl
{
    public IconView()
    {
        InitializeComponent();
    }

    public static readonly DependencyProperty GeometryProperty = DependencyProperty.Register(
        nameof(Geometry), typeof(Geometry), typeof(IconView), new PropertyMetadata(null));

    public static readonly DependencyProperty StrokeProperty = DependencyProperty.Register(
        nameof(Stroke), typeof(Brush), typeof(IconView), new PropertyMetadata(null));

    public static readonly DependencyProperty FillProperty = DependencyProperty.Register(
        nameof(Fill), typeof(Brush), typeof(IconView), new PropertyMetadata(null));

    public static readonly DependencyProperty StrokeThicknessProperty = DependencyProperty.Register(
        nameof(StrokeThickness), typeof(double), typeof(IconView), new PropertyMetadata(2.0));

    public Geometry? Geometry
    {
        get => (Geometry?)GetValue(GeometryProperty);
        set => SetValue(GeometryProperty, value);
    }

    public Brush? Stroke
    {
        get => (Brush?)GetValue(StrokeProperty);
        set => SetValue(StrokeProperty, value);
    }

    public Brush? Fill
    {
        get => (Brush?)GetValue(FillProperty);
        set => SetValue(FillProperty, value);
    }

    public double StrokeThickness
    {
        get => (double)GetValue(StrokeThicknessProperty);
        set => SetValue(StrokeThicknessProperty, value);
    }
}
