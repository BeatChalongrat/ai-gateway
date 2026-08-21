using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Threading;
using FluentAssertions;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// What animating the wheel actually costs.
///
/// Today one notch is one layout pass. Animated, it is one per frame —
/// roughly twelve over a 200 ms curve. The bet is that the same realization
/// work spread across frames beats the same work concentrated in one visible
/// hitch, but a row that overruns a 16 ms frame budget overruns it either way,
/// and the failure mode changes shape: one large jump becomes several small
/// stutters.
///
/// This measures the per-scroll-step LAYOUT cost, which is the part the
/// animation multiplies.
///
/// WHAT IT CANNOT MEASURE: a display-math row owns a live WebView2, and a
/// WebView2 does nothing until it is Loaded into a rendered window with the
/// runtime attached. Headless, MathBlockControl is a cheap empty Grid. So the
/// math numbers below are a FLOOR — the real cost of that case needs a GUI
/// session and is called out as unmeasured in the report.
/// </summary>
public class WheelFrameCostTests
{
    private const double ColumnWidth = 768;
    private const double ViewportHeight = 600;

    private static void RunSta(Action action)
    {
        Exception? failure = null;
        var t = new Thread(() =>
        {
            try { action(); }
            catch (Exception ex) { failure = ex; }
            finally { Dispatcher.CurrentDispatcher.InvokeShutdown(); }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        if (failure is not null) throw failure;
    }

    private static void Pump()
    {
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.BeginInvoke(
            new Action(() => frame.Continue = false), DispatcherPriority.Loaded);
        Dispatcher.PushFrame(frame);
    }

    private static ListBox BuildList(IEnumerable<MessageViewModel> items)
    {
        var template = new DataTemplate(typeof(MessageViewModel));
        var viewer = new FrameworkElementFactory(typeof(MarkdownViewer));
        viewer.SetBinding(MarkdownViewer.MarkdownProperty, new Binding(nameof(MessageViewModel.RenderContent)));
        viewer.SetValue(FrameworkElement.WidthProperty, ColumnWidth);
        template.VisualTree = viewer;
        template.Seal();

        var list = new ListBox
        {
            ItemsSource = items,
            ItemTemplate = template,
            Width = ColumnWidth,
            Height = ViewportHeight,
        };
        VirtualizingPanel.SetIsVirtualizing(list, true);
        VirtualizingPanel.SetVirtualizationMode(list, VirtualizationMode.Recycling);
        VirtualizingPanel.SetScrollUnit(list, ScrollUnit.Pixel);
        return list;
    }

    private static List<MessageViewModel> Thread(int count, bool withMath)
    {
        var messages = new List<MessageViewModel>();
        for (var i = 0; i < count; i++)
        {
            var body = withMath && i % 3 == 0
                ? $"Consider the identity below.\n\n$$ E_{i} = mc^2 + \\sum_{{k=1}}^{{n}} a_k x^k $$\n\nWhich follows."
                : $"Message {i} with **bold**, `code`, a [link](https://example.com) and enough prose "
                  + "that the paragraph wraps across several lines in the 768 pixel column.";
            messages.Add(new MessageViewModel($"m{i}", i % 2 == 0 ? MessageRole.User : MessageRole.Assistant,
                body, DateTimeOffset.UtcNow.AddMinutes(i)));
        }
        return messages;
    }

    /// <summary>Median ms for one scroll step's layout, over a sweep.</summary>
    private static (double Median, double Worst) MeasureStepCost(bool withMath)
    {
        MarkdownViewer.ClearRenderCache();
        var messages = Thread(30, withMath);
        var list = BuildList(messages);
        var window = new Window
        {
            Content = list, Width = ColumnWidth + 40, Height = ViewportHeight + 40,
            Left = -32000, Top = -32000, ShowActivated = false,
            ShowInTaskbar = false, WindowStyle = WindowStyle.None,
        };
        window.Show();
        list.UpdateLayout();
        Pump();

        try
        {
            var scroll = FindScroll(list)!;
            var samples = new List<double>();
            // Warm once so first-render cost is not counted as frame cost.
            for (var i = 0; i < messages.Count; i++) { list.ScrollIntoView(messages[i]); list.UpdateLayout(); }
            Pump();
            scroll.ScrollToVerticalOffset(0);
            list.UpdateLayout();

            var sw = new Stopwatch();
            for (var offset = 0.0; offset < scroll.ExtentHeight - ViewportHeight; offset += 20)
            {
                sw.Restart();
                scroll.ScrollToVerticalOffset(offset); // 20 px ~ one animated frame
                list.UpdateLayout();
                sw.Stop();
                samples.Add(sw.Elapsed.TotalMilliseconds);
            }
            samples.Sort();
            return (samples[samples.Count / 2], samples[^1]);
        }
        finally { window.Close(); }
    }

    private static ScrollViewer? FindScroll(DependencyObject root)
    {
        for (var i = 0; i < System.Windows.Media.VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(root, i);
            if (child is ScrollViewer sv) return sv;
            if (FindScroll(child) is { } nested) return nested;
        }
        return null;
    }

    [Fact]
    public void PerFrameLayoutCost_IsRecorded()
    {
        RunSta(() =>
        {
            var plain = MeasureStepCost(withMath: false);
            var math = MeasureStepCost(withMath: true);

            TestOutput.Record(
                $"[frame] prose-only: median={plain.Median:F2}ms worst={plain.Worst:F2}ms | " +
                $"with display math: median={math.Median:F2}ms worst={math.Worst:F2}ms " +
                $"(frame budget 16.7ms; WebView2 NOT initialised headless — math is a floor)");

            plain.Median.Should().BeGreaterThan(0);
            math.Median.Should().BeGreaterThan(0);
        });
    }
}
