using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

namespace SranDesktop.ReliabilityFixture;

public partial class MainWindow : Window
{
    private bool _moved;
    private bool _visualMoved;

    public MainWindow()
    {
        InitializeComponent();
        ReplaceTarget();
    }

    private void ActivateButton_OnClick(object sender, RoutedEventArgs e) =>
        State.Text = "Button activated";

    private void Toggle_OnChanged(object sender, RoutedEventArgs e) =>
        State.Text = Toggle.IsChecked == true ? "Checkbox enabled" : "Checkbox disabled";

    private void OpenModal_OnClick(object sender, RoutedEventArgs e)
    {
        var dialog = new Window
        {
            Title = "SRAN Reliability Modal",
            Owner = this,
            Width = 320,
            Height = 180,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Content = new TextBlock
            {
                Text = "Fixture modal ready",
                Margin = new Thickness(24)
            }
        };
        dialog.ShowDialog();
        State.Text = "Modal closed";
    }

    private void MoveTarget_OnClick(object sender, RoutedEventArgs e)
    {
        _moved = !_moved;
        MovingTarget.Margin = new Thickness(_moved ? 280 : 20, 80, 0, 0);
        State.Text = "Target moved";
    }

    private void ReplaceTarget_OnClick(object sender, RoutedEventArgs e)
    {
        ReplaceTarget();
        State.Text = "Target replaced";
    }

    private void ReplaceTarget()
    {
        var replacement = new Button
        {
            Content = "Replaceable fixture action",
            Width = 180,
            Height = 36,
            HorizontalAlignment = HorizontalAlignment.Left
        };
        AutomationProperties.SetName(replacement, "Replaceable fixture action");
        replacement.Click += (_, _) => State.Text = "Replacement activated";
        ReplacementHost.Child = replacement;
    }

    private void MovingTarget_OnClick(object sender, RoutedEventArgs e) =>
        State.Text = "Moving target activated";

    private void OcrTarget_OnClick(object sender, RoutedEventArgs e) =>
        State.Text = "OCR target activated";

    private void VisualTarget_OnClick(object sender, RoutedEventArgs e) =>
        State.Text = "Visual target activated";

    private void MoveVisualTarget_OnClick(object sender, RoutedEventArgs e)
    {
        _visualMoved = !_visualMoved;
        VisualTarget.Margin = new Thickness(_visualMoved ? 300 : 20, 360, 0, 0);
        State.Text = "Visual target moved";
    }

    private void AfterScroll_OnClick(object sender, RoutedEventArgs e) =>
        State.Text = "After scroll activated";
}
