using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.Services;

namespace SranDesktop.Presentation.Controls;

/// <summary>Display row for one toast — text resolved through the active
/// locale at creation time; the underlying ToastItem is the dismiss handle.</summary>
public sealed class ToastRow
{
    public required ToastItem Item { get; init; }
    public required string Text { get; init; }
    public required Geometry IconGeometry { get; init; }
    public required Brush IconBrush { get; init; }
}

/// <summary>
/// Mounts once in MainWindow (Phase 8 Task 1): renders ToastService.Instance's
/// visible set as themed cards, pumps expiry on a 250 ms timer, dismisses on
/// click. Row sync is incremental (add new / remove gone by item identity) so
/// an existing toast never re-runs its entrance animation when a sibling
/// appears or leaves.
/// </summary>
public partial class ToastHost : UserControl
{
    private readonly ToastService _service = ToastService.Instance;
    private readonly ObservableCollection<ToastRow> _rows = new();
    private DispatcherTimer? _timer;

    public ToastHost()
    {
        InitializeComponent();
        Items.ItemsSource = _rows;
        Loaded += (_, _) =>
        {
            _service.Changed += OnServiceChanged;
            SyncRows();
        };
        Unloaded += (_, _) =>
        {
            _service.Changed -= OnServiceChanged;
            _timer?.Stop();
        };
    }

    private void OnServiceChanged(object? sender, EventArgs e) =>
        Dispatcher.InvokeAsync(SyncRows);

    private void SyncRows()
    {
        var visible = _service.Visible;

        for (var i = _rows.Count - 1; i >= 0; i--)
        {
            if (!visible.Contains(_rows[i].Item))
                _rows.RemoveAt(i);
        }
        foreach (var item in visible)
        {
            if (_rows.All(r => !ReferenceEquals(r.Item, item)))
                _rows.Add(BuildRow(item));
        }

        if (_rows.Count > 0)
        {
            _timer ??= CreateTimer();
            if (!_timer.IsEnabled)
                _timer.Start();
        }
        else
        {
            _timer?.Stop();
        }
    }

    private DispatcherTimer CreateTimer()
    {
        var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(250) };
        timer.Tick += (_, _) => _service.ExpireDue();
        return timer;
    }

    private ToastRow BuildRow(ToastItem item)
    {
        var text = LocaleService.Instance.T(item.MessageKey);
        if (!string.IsNullOrEmpty(item.Detail))
            text = $"{text} — {item.Detail}";

        var (geometryKey, brushKey) = item.Kind switch
        {
            ToastKind.Success => ("Geo_Check", "AccentGreenBrush"),
            ToastKind.Error => ("Geo_AlertCircle", "AccentRedBrush"),
            _ => ("Geo_AlertCircle", "TextSecondaryBrush")
        };
        return new ToastRow
        {
            Item = item,
            Text = text,
            IconGeometry = (Geometry)FindResource(geometryKey),
            IconBrush = (Brush)FindResource(brushKey)
        };
    }

    private void Card_MouseLeftButtonUp(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: ToastRow row })
            _service.Dismiss(row.Item);
    }

    /// <summary>Entrance: fade + 10px slide-up within the motion budget;
    /// snaps instantly when the OS reduced-motion setting is on.</summary>
    private void Card_Loaded(object sender, RoutedEventArgs e)
    {
        if (sender is Border card)
            RunEntranceAnimation(card);
    }

    /// <summary>A toast is cosmetic — this must never throw. The template's
    /// transform can be a frozen shared Freezable (see
    /// <see cref="PrepareEntranceTransform"/>); if anything still can't be
    /// animated, the card snaps to rest instead of crashing the app.</summary>
    internal static void RunEntranceAnimation(Border card)
    {
        var duration = MotionSettings.Toast;
        var ease = new CubicEase { EasingMode = EasingMode.EaseOut };
        try
        {
            card.BeginAnimation(UIElement.OpacityProperty,
                new DoubleAnimation(0, 1, duration) { EasingFunction = ease });
            var translate = PrepareEntranceTransform(card);
            translate.BeginAnimation(TranslateTransform.YProperty,
                new DoubleAnimation(10, 0, duration) { EasingFunction = ease });
        }
        catch (Exception)
        {
            card.Opacity = 1;
            if (card.RenderTransform is TranslateTransform { IsFrozen: false } rest)
                rest.Y = 0;
        }
    }

    /// <summary>WPF freezes a constant <c>&lt;TranslateTransform /&gt;</c>
    /// declared in a DataTemplate and shares one instance across all cards;
    /// calling BeginAnimation on it throws InvalidOperationException. Always
    /// hand the animation a transform this card owns and can animate.</summary>
    internal static TranslateTransform PrepareEntranceTransform(Border card)
    {
        if (card.RenderTransform is TranslateTransform existing && !existing.IsFrozen)
            return existing;
        var fresh = new TranslateTransform();
        card.RenderTransform = fresh;
        return fresh;
    }
}
