using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Animation;
using System.Windows.Shapes;
using System.Windows.Threading;
using Markdig.Syntax;
using SranDesktop.Presentation.Rendering;

namespace SranDesktop.Presentation.Controls;

/// <summary>
/// Displays markdown. While <see cref="IsStreaming"/> is true the control
/// renders PROGRESSIVELY: the source is diffed at block level (BlockDiff) and
/// only blocks whose source changed — in practice the growing last block plus
/// newly appeared ones — are re-built, at most every 150 ms on Background
/// priority so token appends and the Phase 3 draft timer always win. Plan and
/// artifact fences are hidden behind an indicator chip (StreamingFenceGuard).
/// When streaming ends, ONE full clean render replaces the incremental state
/// — it is authoritative. Non-streaming updates keep the original behavior:
/// first render immediate, subsequent changes coalesced through a 66 ms timer.
/// </summary>
public sealed class MarkdownViewer : ContentControl
{
    private static readonly MarkdownRenderer Renderer = new();
    private static readonly TimeSpan RenderInterval = TimeSpan.FromMilliseconds(66);
    private static readonly TimeSpan StreamRenderInterval = TimeSpan.FromMilliseconds(150);

    private DispatcherTimer? _timer;
    private bool _renderQueued;

    // ── incremental streaming state ──
    private DispatcherTimer? _streamTimer;
    private bool _streamDirty;
    private StackPanel? _streamPanel;
    private List<BlockFingerprint>? _streamFingerprints;
    private readonly List<FrameworkElement?> _streamElements = new();
    private FrameworkElement? _fenceChip;
    private StreamingFenceKind _fenceKind;

    public static readonly DependencyProperty MarkdownProperty = DependencyProperty.Register(
        nameof(Markdown), typeof(string), typeof(MarkdownViewer),
        new PropertyMetadata(string.Empty, OnMarkdownChanged));

    public static readonly DependencyProperty IsStreamingProperty = DependencyProperty.Register(
        nameof(IsStreaming), typeof(bool), typeof(MarkdownViewer),
        new PropertyMetadata(false, OnIsStreamingChanged));

    public string Markdown
    {
        get => (string)GetValue(MarkdownProperty);
        set => SetValue(MarkdownProperty, value);
    }

    /// <summary>When true, renders incrementally instead of per-change.</summary>
    public bool IsStreaming
    {
        get => (bool)GetValue(IsStreamingProperty);
        set => SetValue(IsStreamingProperty, value);
    }

    public MarkdownViewer()
    {
        Focusable = false;
        IsTabStop = false;
        // Phase 8 Task 3 (stale-flash fix): when this viewer is RECYCLED onto
        // a different message, the previous message's rendered panel must
        // never show — clear immediately, before any timer tick. If the new
        // message's Markdown happens to EQUAL the old value the DP callback
        // never fires, so re-render at Loaded priority (after bindings have
        // delivered) when nothing else has.
        DataContextChanged += (_, _) =>
        {
            _timer?.Stop();
            _renderQueued = false;
            _streamTimer?.Stop();
            ResetStreamingState();
            Content = null;
            Dispatcher.InvokeAsync(() =>
            {
                if (Content is null && !IsStreaming)
                    RenderNow();
            }, DispatcherPriority.Loaded);
        };
    }

    private static void OnMarkdownChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        var viewer = (MarkdownViewer)d;

        if (viewer.IsStreaming)
        {
            viewer._streamDirty = true;
            viewer.EnsureStreamTimer();
            return;
        }

        // First value renders immediately (history load, completed messages);
        // subsequent changes are throttled.
        if (viewer.Content is null)
        {
            viewer.RenderNow();
            return;
        }

        viewer._renderQueued = true;
        viewer._timer ??= viewer.CreateTimer();
        if (!viewer._timer.IsEnabled)
            viewer._timer.Start();
    }

    private static void OnIsStreamingChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        var viewer = (MarkdownViewer)d;
        if (e.NewValue is true)
        {
            viewer.ResetStreamingState();
            // Stale-flash fix (Phase 8): a viewer flipping to streaming must
            // show an EMPTY panel until the first stream tick — never the
            // previous content (the typing dots cover the visual gap).
            viewer.Content = null;
            viewer._streamDirty = true;
            viewer.EnsureStreamTimer();
        }
        else
        {
            // Streaming just ended → ONE full clean render. Never trust the
            // incremental state (fence chips, skipped ticks) as final.
            viewer._streamTimer?.Stop();
            viewer.ResetStreamingState();
            viewer.RenderNow();
        }
    }

    private void ResetStreamingState()
    {
        _streamDirty = false;
        _streamPanel = null;
        _streamFingerprints = null;
        _streamElements.Clear();
        _fenceChip = null;
        _fenceKind = StreamingFenceKind.None;
    }

    private void EnsureStreamTimer()
    {
        _streamTimer ??= CreateStreamTimer();
        if (!_streamTimer.IsEnabled)
            _streamTimer.Start();
    }

    private DispatcherTimer CreateStreamTimer()
    {
        // Background priority: input handling, token appends and the Phase 3
        // draft-save timer all outrank streaming re-renders.
        var timer = new DispatcherTimer(DispatcherPriority.Background)
        {
            Interval = StreamRenderInterval
        };
        timer.Tick += (_, _) =>
        {
            if (!IsStreaming || !_streamDirty)
            {
                timer.Stop();
                return;
            }
            _streamDirty = false;
            RenderStreamingIncrement();
        };
        return timer;
    }

    private void RenderStreamingIncrement()
    {
        var (visible, fenceKind) = StreamingFenceGuard.TruncateAtOpenFence(Markdown ?? string.Empty);

        MarkdownDocument document;
        try
        {
            document = MarkdownRenderer.ParseDocument(visible);
        }
        catch (Exception)
        {
            return; // malformed mid-stream fragment — the final render recovers
        }

        if (_streamPanel is null || !ReferenceEquals(Content, _streamPanel))
        {
            _streamPanel = new StackPanel();
            _streamFingerprints = null;
            _streamElements.Clear();
            _fenceChip = null;
            Content = _streamPanel;
        }

        var fingerprints = BlockDiff.Fingerprint(visible, document);
        var blocks = document.ToList();
        var firstChanged = _streamFingerprints is null
            ? 0
            : BlockDiff.FirstChangedIndex(_streamFingerprints, fingerprints);

        // The chip always sits last — detach it before touching blocks.
        if (_fenceChip is not null)
            _streamPanel.Children.Remove(_fenceChip);

        // Drop stale elements from the first changed block onward.
        // _streamElements is index-aligned with the block list (nulls for
        // blocks the renderer skips), so panel children stay consistent.
        for (var i = _streamElements.Count - 1; i >= firstChanged; i--)
        {
            if (_streamElements[i] is { } stale)
                _streamPanel.Children.Remove(stale);
            _streamElements.RemoveAt(i);
        }

        // Re-render the changed tail with the SAME per-block path as the
        // full render (identical output).
        for (var i = firstChanged; i < blocks.Count; i++)
        {
            FrameworkElement? element = null;
            try
            {
                element = Renderer.RenderBlock(blocks[i], streaming: true);
            }
            catch (Exception)
            {
                // Skip the block this tick; the final render is authoritative.
            }
            if (element is not null)
                _streamPanel.Children.Add(element);
            _streamElements.Add(element);
        }

        _streamFingerprints = fingerprints;

        if (fenceKind is StreamingFenceKind.Plan or StreamingFenceKind.Artifact)
        {
            if (_fenceChip is null || _fenceKind != fenceKind)
                _fenceChip = BuildFenceChip(fenceKind);
            _fenceKind = fenceKind;
            _streamPanel.Children.Add(_fenceChip);
        }
        else
        {
            _fenceChip = null;
            _fenceKind = StreamingFenceKind.None;
        }
    }

    /// <summary>Spinner-dot + localized label shown while a plan/artifact
    /// fence streams (its raw body is hidden by StreamingFenceGuard).</summary>
    private static FrameworkElement BuildFenceChip(StreamingFenceKind kind)
    {
        var dot = new Ellipse
        {
            Width = 8,
            Height = 8,
            Margin = new Thickness(0, 0, 8, 0),
            VerticalAlignment = VerticalAlignment.Center
        };
        dot.SetResourceReference(Shape.FillProperty, "AccentBrush");
        dot.BeginAnimation(OpacityProperty, new DoubleAnimation(0.3, 1, TimeSpan.FromMilliseconds(600))
        {
            AutoReverse = true,
            RepeatBehavior = RepeatBehavior.Forever
        });

        var label = new TextBlock
        {
            FontSize = 13,
            VerticalAlignment = VerticalAlignment.Center,
            Text = Localization.LocaleService.Instance.T(kind == StreamingFenceKind.Plan
                ? "stream.preparingDoc"
                : "stream.preparingArtifact")
        };
        label.SetResourceReference(TextBlock.ForegroundProperty, "TextSecondaryBrush");

        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(dot);
        row.Children.Add(label);

        var chip = new Border
        {
            Child = row,
            CornerRadius = new CornerRadius(10),
            Padding = new Thickness(12, 6, 12, 6),
            Margin = new Thickness(0, 8, 0, 8),
            HorizontalAlignment = HorizontalAlignment.Left
        };
        chip.SetResourceReference(Border.BackgroundProperty, "BgSecondaryBrush");
        return chip;
    }

    private DispatcherTimer CreateTimer()
    {
        var timer = new DispatcherTimer(DispatcherPriority.Render) { Interval = RenderInterval };
        timer.Tick += (_, _) =>
        {
            if (_renderQueued)
            {
                _renderQueued = false;
                RenderNow();
            }
            else
            {
                timer.Stop();
            }
        };
        return timer;
    }

    private void RenderNow() => Content = Renderer.Render(Markdown ?? string.Empty);
}
