using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using SranDesktop.Presentation.ViewModels;

namespace SranDesktop.Presentation.Controls;

public partial class MessageBubble : UserControl
{
    private DispatcherTimer? _copyRevertTimer;

    public MessageBubble()
    {
        InitializeComponent();
        // Both hooks are needed: Loaded covers freshly created containers,
        // DataContextChanged covers RECYCLED ones (Loaded doesn't re-fire).
        Loaded += (_, _) => TryAnimateEntrance();
        DataContextChanged += (_, _) => TryAnimateEntrance();
    }

    /// <summary>Breathing animation for the cat status mark (was: pulsing
    /// dots). Started on visible / stopped on collapse so no animation clock
    /// ticks for hidden bubbles; skipped entirely under reduced motion —
    /// the cat then sits still, mirroring the web's prefers-reduced-motion
    /// rule for .cat-indicator.</summary>
    private void CatIndicator_IsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (sender is not System.Windows.Shapes.Path path ||
            path.RenderTransform is not ScaleTransform scale)
            return;

        if (e.NewValue is true && Services.MotionSettings.Enabled)
        {
            var breathe = new System.Windows.Media.Animation.DoubleAnimation(1.0, 1.06,
                TimeSpan.FromSeconds(1.1))
            {
                AutoReverse = true,
                RepeatBehavior = System.Windows.Media.Animation.RepeatBehavior.Forever,
                EasingFunction = new System.Windows.Media.Animation.SineEase
                {
                    EasingMode = System.Windows.Media.Animation.EasingMode.EaseInOut
                }
            };
            scale.BeginAnimation(ScaleTransform.ScaleXProperty, breathe);
            scale.BeginAnimation(ScaleTransform.ScaleYProperty, breathe);
        }
        else
        {
            scale.BeginAnimation(ScaleTransform.ScaleXProperty, null);
            scale.BeginAnimation(ScaleTransform.ScaleYProperty, null);
        }
    }

    /// <summary>Shimmer sweep for the cat row's status label (web parity with
    /// .shimmer-text): translates the label's gradient Foreground so the
    /// bright band sweeps left→right on a loop. Started on visible / stopped
    /// on collapse; under reduced motion the label falls back to the flat
    /// theme brush instead of showing a frozen highlight band.</summary>
    private void CatStatusRow_IsVisibleChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        foreach (var tb in new[] { CatStatusLabel, CatStatusFallback })
        {
            if (tb?.Foreground is not LinearGradientBrush brush ||
                brush.RelativeTransform is not TranslateTransform translate)
                continue;

            if (e.NewValue is true && Services.MotionSettings.Enabled)
            {
                var sweep = new System.Windows.Media.Animation.DoubleAnimation(
                    -1.0, 1.0, TimeSpan.FromSeconds(1.9))
                {
                    RepeatBehavior = System.Windows.Media.Animation.RepeatBehavior.Forever
                };
                translate.BeginAnimation(TranslateTransform.XProperty, sweep);
            }
            else
            {
                translate.BeginAnimation(TranslateTransform.XProperty, null);
                if (!Services.MotionSettings.Enabled)
                    tb.SetResourceReference(ForegroundProperty, "TextSecondaryBrush");
            }
        }
    }

    /// <summary>Phase 8 Task 3: fade + 8px slide-up (140 ms ease-out) for live
    /// appends only; everything else snaps to rest. Uses RenderTransform, so
    /// layout — and therefore the auto-scroll anchor — never moves.</summary>
    private void TryAnimateEntrance()
    {
        if (Root.RenderTransform is not System.Windows.Media.TranslateTransform translate)
            return;

        // Reset to rest first — a recycled container may carry a mid-flight value.
        Root.BeginAnimation(OpacityProperty, null);
        translate.BeginAnimation(System.Windows.Media.TranslateTransform.YProperty, null);
        Root.Opacity = 1;
        translate.Y = 0;

        if (DataContext is not MessageViewModel { AnimateEntrance: true } vm)
            return;
        vm.AnimateEntrance = false; // one-shot
        if (!Services.MotionSettings.Enabled)
            return;

        var duration = Services.MotionSettings.Entrance;
        var ease = new System.Windows.Media.Animation.CubicEase
        {
            EasingMode = System.Windows.Media.Animation.EasingMode.EaseOut
        };
        Root.BeginAnimation(OpacityProperty,
            new System.Windows.Media.Animation.DoubleAnimation(0, 1, duration) { EasingFunction = ease });
        translate.BeginAnimation(System.Windows.Media.TranslateTransform.YProperty,
            new System.Windows.Media.Animation.DoubleAnimation(8, 0, duration) { EasingFunction = ease });
    }

    private void CopyButton_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is not MessageViewModel vm || vm.Content.Length == 0)
            return;
        if (sender is not Button { Content: IconView icon })
            return;

        try
        {
            Clipboard.SetText(vm.Content);
            SranDesktop.Application.Services.ToastService.Instance.Show(
                SranDesktop.Application.Interfaces.ToastKind.Success, "toast.copied");

            // Flash a green check, then revert to the copy glyph.
            icon.Geometry = (Geometry)FindResource("Geo_Check");
            icon.Stroke = (Brush)FindResource("AccentGreenBrush");

            _copyRevertTimer?.Stop();
            _copyRevertTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            _copyRevertTimer.Tick += (_, _) =>
            {
                icon.Geometry = (Geometry)FindResource("Geo_Copy");
                icon.Stroke = (Brush)FindResource("TextSecondaryBrush");
                _copyRevertTimer.Stop();
            };
            _copyRevertTimer.Start();
        }
        catch (Exception)
        {
            // Clipboard can be locked by another process — non-fatal.
        }
    }

    // ---------- inline video controls (R7 B3) ----------

    private void VideoPlayPause_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button
            || button.DataContext is not InlineAttachmentViewModel vm
            || button.Parent is not Grid grid
            || FindMediaElement(grid) is not { } player)
            return;

        if (vm.IsPlaying)
        {
            player.Pause();
            vm.IsPlaying = false;
        }
        else
        {
            player.Play();
            vm.IsPlaying = true;
        }
    }

    private void Video_MediaOpened(object sender, RoutedEventArgs e)
    {
        // Show the first frame as a poster instead of a black box.
        if (sender is MediaElement player)
        {
            player.Play();
            player.Pause();
        }
    }

    private void Video_MediaEnded(object sender, RoutedEventArgs e)
    {
        if (sender is MediaElement { DataContext: InlineAttachmentViewModel vm } player)
        {
            player.Stop();
            vm.IsPlaying = false;
        }
    }

    private static MediaElement? FindMediaElement(DependencyObject root)
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = VisualTreeHelper.GetChild(root, i);
            if (child is MediaElement media)
                return media;
            if (FindMediaElement(child) is { } nested)
                return nested;
        }
        return null;
    }
}
