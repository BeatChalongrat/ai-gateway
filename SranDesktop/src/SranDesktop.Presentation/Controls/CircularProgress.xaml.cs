using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SranDesktop.Presentation.Controls;

/// <summary>Circular progress ring with a percentage label (usage popup).</summary>
public partial class CircularProgress : UserControl
{
    public static readonly DependencyProperty PercentageProperty =
        DependencyProperty.Register(nameof(Percentage), typeof(double), typeof(CircularProgress),
            new PropertyMetadata(0.0, OnPercentageChanged));

    public double Percentage
    {
        get => (double)GetValue(PercentageProperty);
        set => SetValue(PercentageProperty, value);
    }

    public CircularProgress()
    {
        InitializeComponent();
        UpdateArc();
    }

    private static void OnPercentageChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
        => ((CircularProgress)d).UpdateArc();

    private void UpdateArc()
    {
        var pct = Math.Clamp(Percentage, 0, 100);
        Label.Text = $"{pct:0}%";

        const double radius = 20;
        const double cx = 24;
        const double cy = 24;

        if (pct <= 0)
        {
            ArcPath.Data = null;
            return;
        }

        // Full circles can't be drawn with a single arc segment — fake 100%
        // with 99.99% so start and end points don't coincide.
        var angle = Math.Min(pct, 99.99) / 100.0 * 360.0;
        var radians = (angle - 90) * Math.PI / 180.0;
        var endX = cx + radius * Math.Cos(radians);
        var endY = cy + radius * Math.Sin(radians);

        var figure = new PathFigure { StartPoint = new Point(cx, cy - radius) };
        figure.Segments.Add(new ArcSegment(
            new Point(endX, endY),
            new Size(radius, radius),
            0,
            angle > 180,
            SweepDirection.Clockwise,
            true));
        ArcPath.Data = new PathGeometry { Figures = { figure } };
    }
}
