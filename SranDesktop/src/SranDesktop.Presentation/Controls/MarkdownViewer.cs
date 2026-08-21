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

    /// <summary>
    /// Rendered trees, shared by every viewer — a recycled row re-attaches its
    /// message instead of re-parsing it. Theme and locale are part of the key,
    /// so stale entries are already unreachable after a switch; clearing on
    /// those events just releases them promptly instead of waiting for the LRU
    /// to age them out.
    /// </summary>
    private static readonly RenderedMessageCache Cache = new(capacity: 60);

    static MarkdownViewer()
    {
        Services.ThemeManager.ThemeChanged += (_, _) => Cache.Clear();
        Localization.LocaleService.Instance.LocaleChanged += (_, _) => Cache.Clear();
    }

    /// <summary>Test seam — drop every cached tree.</summary>
    internal static void ClearRenderCache() => Cache.Clear();

    internal static int CachedTreeCount => Cache.Count;

    // ── TEMPORARY (scroll-perf Step 0) ──────────────────────────────────────
    // Counts full and incremental renders so the recycle cost can be measured
    // instead of argued about. Read by ScrollRenderCostTests; removed in its
    // own commit once the before/after numbers are recorded.
    internal static int FullRenderCount;
    internal static int StreamingIncrementCount;
    internal static int CacheHitCount;

    /// <summary>Receives the markdown of every full render, so a test can tell
    /// a first render apart from a re-render of the same message.</summary>
    internal static Action<string>? OnFullRender;

    internal static void ResetRenderCounters()
    {
        FullRenderCount = 0;
        StreamingIncrementCount = 0;
        CacheHitCount = 0;
    }
    // ── end TEMPORARY ───────────────────────────────────────────────────────

    private DispatcherTimer? _timer;
    private bool _renderQueued;

    /// <summary>The exact string the current <see cref="ContentControl.Content"/>
    /// was built from, so a recycled viewer can tell "already showing the right
    /// thing" from "showing the previous message".</summary>
    private string? _renderedSource;

    /// <summary>Set by DataContextChanged, cleared by whichever runs first:
    /// the Markdown binding delivering the new message (render synchronously)
    /// or the Loaded-priority fallback (the texts were identical, nothing to
    /// do). See the constructor.</summary>
    private bool _pendingSwap;

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
        // never show. That still holds — but it is now solved by ORDERING
        // rather than by blanking.
        //
        // Measured (ScrollRenderCostTests.Step1_BindingVersusDataContextChanged_Ordering):
        // on a recycled container DataContextChanged fires BEFORE the Markdown
        // binding delivers. So at this point Markdown still holds the PREVIOUS
        // message's text and cannot simply be re-rendered. The old code set
        // Content = null here and waited; a null Content measures as a
        // zero-height row, and when the new markdown happened to EQUAL the old
        // one the DP callback never fired, so the row stayed empty through
        // layout and only filled at Loaded priority — the height jump.
        //
        // Instead: pull the binding forward so the NEW text is in hand, then
        // swap old → new in ONE assignment. The row's height goes from its old
        // value straight to its new value with no zero in between, and the
        // previous message's panel is never displayed because the swap happens
        // before this generation pass is measured.
        DataContextChanged += (_, _) =>
        {
            _timer?.Stop();
            _renderQueued = false;
            _streamTimer?.Stop();
            ResetStreamingState();

            if (IsStreaming)
            {
                // Streaming shows an empty panel by design — the typing
                // indicator covers the gap. Unchanged.
                Content = null;
                _renderedSource = null;
                _pendingSwap = false;
                return;
            }

            // Do NOT blank. The binding delivers a moment from now, still
            // inside this generation pass and still before the panel measures
            // the container, and OnMarkdownChanged will swap old → new in one
            // assignment. Nothing renders in between, so the previous message
            // is never actually displayed.
            _pendingSwap = true;
            Dispatcher.InvokeAsync(() =>
            {
                if (!_pendingSwap || IsStreaming)
                    return;
                // The binding never fired, which means the new message's text
                // is IDENTICAL to what is already rendered (the case the old
                // code needed this fallback for). Re-rendering it would
                // produce the same tree, so only re-render if the content is
                // somehow not there.
                _pendingSwap = false;
                if (Content is null || !string.Equals(_renderedSource, Markdown, StringComparison.Ordinal))
                    RenderNow();
            }, DispatcherPriority.Loaded);
        };
    }

    private static void OnMarkdownChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        var viewer = (MarkdownViewer)d;

        // This is the delivery a DataContext swap was waiting for: render it
        // NOW, synchronously, so the container carries the new content into
        // the same measure pass. Going through the 66 ms coalescing timer here
        // would leave the PREVIOUS message on screen until the tick.
        if (viewer._pendingSwap && !viewer.IsStreaming)
        {
            viewer._pendingSwap = false;
            viewer.RenderNow();
            return;
        }
        viewer._pendingSwap = false;

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
            viewer._renderedSource = null;
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
        StreamingIncrementCount++; // TEMPORARY (scroll-perf Step 0)
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
            // Incremental content is not a full render of any one source.
            _renderedSource = null;
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

    /// <summary>
    /// Message identity comes from the DataContext REFERENCE, not from a bound
    /// id property. A bound id would be unreliable here: DataContextChanged
    /// fires before any binding delivers, and the render is driven by whichever
    /// binding lands first, so an id binding is routinely still the previous
    /// message's when the key is built (measured: zero cache hits). The
    /// DataContext itself is already correct at this point — it is what raised
    /// the event.
    /// </summary>
    private RenderedMessageCache.Key? CacheKeyFor(string source)
    {
        if (DataContext is not { } owner)
            return null;
        return new RenderedMessageCache.Key(
            owner, source, Localization.LocaleService.Instance.Locale, Services.ThemeManager.IsDark);
    }

    private void RenderNow()
    {
        var source = Markdown ?? string.Empty;
        var key = CacheKeyFor(source);

        if (key is { } hitKey && Cache.TryGet(hitKey, this, out var cached))
        {
            CacheHitCount++;        // TEMPORARY (scroll-perf Step 0)
            Content = cached;
            _renderedSource = source;
            return;
        }

        FullRenderCount++;          // TEMPORARY (scroll-perf Step 0)
        OnFullRender?.Invoke(source); // TEMPORARY (scroll-perf Step 0)

        var katexBefore = MarkdownRenderer.KatexSurfacesCreated;
        var built = Renderer.Render(source);
        Content = built;
        _renderedSource = source;

        // A tree containing a MathBlockControl owns a live WebView2. Caching it
        // would retain a browser frame per entry, which costs far more than the
        // parse it saves — so display-math messages stay uncached and keep
        // today's behaviour exactly.
        if (key is { } putKey && MarkdownRenderer.KatexSurfacesCreated == katexBefore)
            Cache.Put(putKey, built);
    }
}
