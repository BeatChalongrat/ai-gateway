using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Threading;
using FluentAssertions;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Scroll-perf STEP 0 — measure the recycle cost instead of arguing about it.
///
/// Builds the REAL list: a ListBox configured exactly as ChatView.xaml:39-52
/// (IsVirtualizing, VirtualizationMode=Recycling, ScrollUnit=Pixel) whose item
/// template is a MarkdownViewer bound to RenderContent, the same binding
/// MessageBubble.xaml:760 uses. Then it scrolls the viewport down and back and
/// counts MarkdownViewer.FullRenderCount against the number of times a row
/// entered the viewport.
///
/// This is a harness, not the owner's GUI: synthetic messages, no MessageBubble
/// chrome, no WebView2. It measures the MECHANISM (recycle → re-render), which
/// is the thing the fix changes. Absolute frame timings still need a real
/// session.
/// </summary>
public class ScrollRenderCostTests
{
    private const double ViewportHeight = 600;
    private const double ColumnWidth = 768; // ChatView.xaml:85 / MarkdownRenderer.MessageColumnWidth

    // 1x1 transparent PNG — a data: URI takes the synchronous decode path
    // (BitmapCacheOption.OnLoad, no DelayCreation), so its size is known at
    // build time. Remote images are the ones that arrive late.
    private const string TinyPngDataUri =
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

    private static void RunSta(Action action)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try { action(); }
            catch (Exception ex) { failure = ex; }
            finally { Dispatcher.CurrentDispatcher.InvokeShutdown(); }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure is not null)
            throw failure;
    }

    /// <summary>Drain the dispatcher queue down to <paramref name="through"/>,
    /// so DispatcherPriority.Loaded callbacks queued by DataContextChanged
    /// actually run — the same thing a real frame does.</summary>
    private static void Pump(DispatcherPriority through = DispatcherPriority.Loaded)
    {
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.BeginInvoke(
            new Action(() => frame.Continue = false), through);
        Dispatcher.PushFrame(frame);
    }

    private static List<MessageViewModel> BuildThread(int count)
    {
        var messages = new List<MessageViewModel>();
        for (var i = 0; i < count; i++)
        {
            string body;
            if (i == 7)
            {
                body = "Here is a table.\n\n"
                    + "| Column A | Column B | Column C |\n"
                    + "|---|---:|:--:|\n"
                    + "| alpha | 1 | yes |\n"
                    + "| beta with a longer prose cell that must wrap | 22 | no |\n"
                    + "| gamma | 333 | maybe |\n";
            }
            else if (i == 15)
            {
                body = "An inline image follows.\n\n![chart](" + TinyPngDataUri + ")\n";
            }
            else if (i % 5 == 3)
            {
                body = "Some code:\n\n```csharp\npublic void Method(int x)\n{\n    // comment\n    var s = \"text\";\n}\n```\n";
            }
            else
            {
                body = $"Message {i} with **bold**, `code`, a [link](https://example.com) and "
                     + "enough prose that the paragraph wraps across more than a single line "
                     + "in the 768 pixel conversation column used by the real thread.";
            }

            messages.Add(new MessageViewModel(
                $"m{i}",
                i % 2 == 0 ? MessageRole.User : MessageRole.Assistant,
                body,
                DateTimeOffset.UtcNow.AddMinutes(i)));
        }
        return messages;
    }

    /// <summary>The ListBox from ChatView.xaml, minus chrome this test does not
    /// need. Virtualization settings are copied verbatim.</summary>
    private static ListBox BuildList(IEnumerable<MessageViewModel> items)
    {
        var template = new DataTemplate(typeof(MessageViewModel));
        var viewer = new FrameworkElementFactory(typeof(MarkdownViewer));
        viewer.SetBinding(MarkdownViewer.MarkdownProperty, new Binding(nameof(MessageViewModel.RenderContent)));
        viewer.SetBinding(MarkdownViewer.IsStreamingProperty, new Binding(nameof(MessageViewModel.IsStreaming)));
        viewer.SetValue(FrameworkElement.WidthProperty, ColumnWidth);
        template.VisualTree = viewer;
        template.Seal();

        return new ListBox
        {
            ItemsSource = items,
            ItemTemplate = template,
            Width = ColumnWidth,
            Height = ViewportHeight,
        };
    }

    private static void ConfigureVirtualization(ListBox list, VirtualizationCacheLength? cache = null)
    {
        if (cache is { } length)
        {
            VirtualizingPanel.SetCacheLength(list, length);
            VirtualizingPanel.SetCacheLengthUnit(list, VirtualizationCacheLengthUnit.Page);
        }
        VirtualizingPanel.SetIsVirtualizing(list, true);
        VirtualizingPanel.SetVirtualizationMode(list, VirtualizationMode.Recycling);
        VirtualizingPanel.SetScrollUnit(list, ScrollUnit.Pixel);
        ScrollViewer.SetHorizontalScrollBarVisibility(list, ScrollBarVisibility.Disabled);
        ScrollViewer.SetVerticalScrollBarVisibility(list, ScrollBarVisibility.Auto);
    }

    private static ScrollViewer FindScrollViewer(DependencyObject root)
    {
        if (root is ScrollViewer sv)
            return sv;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(root); i++)
        {
            var found = FindScrollViewer(VisualTreeHelper.GetChild(root, i));
            if (found is not null)
                return found;
        }
        return null!;
    }

    /// <summary>A VirtualizingStackPanel only materialises inside a real
    /// ScrollViewer, and ListBox only builds its template under a live
    /// PresentationSource — so the list needs a window. It is opened offscreen
    /// and closed in the caller's finally; nothing is ever visible.</summary>
    private static Window Layout(ListBox list)
    {
        var window = new Window
        {
            Content = list,
            Width = ColumnWidth + 40,
            Height = ViewportHeight + 40,
            Left = -32000,
            Top = -32000,
            ShowActivated = false,
            ShowInTaskbar = false,
            WindowStyle = WindowStyle.None,
        };
        window.Show();
        list.UpdateLayout();
        Pump();
        return window;
    }

    /// <summary>Scrolls top → bottom → top in viewport-sized steps, counting
    /// how many times a row transitions from unrealized to realized. That count
    /// is the denominator: the diagnosis predicts one full render per entry.</summary>
    private static (int Entries, int Renders, int RepeatEntries) ScrollPass(ListBox list, int count)
    {
        var scroll = FindScrollViewer(list);
        scroll.Should().NotBeNull("the ListBox template must produce a ScrollViewer");

        var generator = list.ItemContainerGenerator;
        var realized = new HashSet<int>();
        var everEntered = new HashSet<int>();
        var entries = 0;
        var repeats = 0;

        void Sample()
        {
            var now = new HashSet<int>();
            for (var i = 0; i < count; i++)
                if (generator.ContainerFromIndex(i) is not null)
                    now.Add(i);

            foreach (var i in now)
            {
                if (realized.Contains(i))
                    continue;
                entries++;
                if (!everEntered.Add(i))
                    repeats++;
            }
            realized = now;
        }

        Sample();
        // Re-arm AFTER the initial realization so the pass measures scrolling,
        // not first paint.
        MarkdownViewer.ResetRenderCounters();

        // Drive by ITEM, not by pixel offset. A VirtualizingStackPanel in pixel
        // mode estimates its extent from the average height of the rows it has
        // realized so far, so an offset-based loop runs a different number of
        // steps on each run — and on each code version, which makes a
        // before/after comparison meaningless. Walking every index down and
        // back up is the same workload every time.
        for (var i = 0; i < count; i++)
        {
            list.ScrollIntoView(list.Items[i]);
            list.UpdateLayout();
            Pump();
            Sample();
        }
        for (var i = count - 1; i >= 0; i--)
        {
            list.ScrollIntoView(list.Items[i]);
            list.UpdateLayout();
            Pump();
            Sample();
        }

        return (entries, MarkdownViewer.FullRenderCount, repeats);
    }

    [Fact]
    public void Step0_RendersPerRowEntry_OverOneScrollPass() => MeasurePass(null, "default");

    /// <summary>STEP 4 — does a bigger realization window help, now that the
    /// render cache exists? Recorded rather than assumed; kept only if the
    /// numbers move.</summary>
    [Fact]
    public void Step4_RendersPerRowEntry_WithCacheLength() =>
        MeasurePass(new VirtualizationCacheLength(1, 1), "CacheLength=1 page");

    private static void MeasurePass(VirtualizationCacheLength? cacheLength, string label)
    {
        RunSta(() =>
        {
            MarkdownViewer.ClearRenderCache();
            var messages = BuildThread(32);
            var list = BuildList(messages);
            ConfigureVirtualization(list, cacheLength);
            // Per-message ledger: the prompt asks how many renders were for a
            // message ALREADY rendered earlier in the same pass. Container
            // realization is too coarse to answer that (the panel can realize
            // and trim a row between samples), so count the renders themselves.
            var rendersByMessage = new Dictionary<string, int>(StringComparer.Ordinal);
            MarkdownViewer.OnFullRender = md =>
            {
                rendersByMessage.TryGetValue(md, out var n);
                rendersByMessage[md] = n + 1;
            };

            var window = Layout(list);
            try
            {
                rendersByMessage.Clear(); // first paint is not the measurement
                var (entries, renders, repeats) = ScrollPass(list, messages.Count);

                var distinct = rendersByMessage.Count;
                var reRenders = rendersByMessage.Values.Sum(n => n - 1);

                TestOutput.Record(
                    $"[{label}] entries={entries} renders={renders} " +
                    $"hits={MarkdownViewer.CacheHitCount} repeatEntries={repeats} " +
                    $"rendersPerEntry={(double)renders / entries:F2} " +
                    $"distinctMessagesRendered={distinct} reRendersOfAlreadyRendered={reRenders} " +
                    $"rendersPerMessage={(double)renders / Math.Max(1, distinct):F2}");

                // Recorded for the report; the assertion only pins that the
                // measurement ran and that rows really were recycled.
                entries.Should().BeGreaterThan(messages.Count,
                    "a top→bottom→top pass must re-enter rows, i.e. recycling is active");
                renders.Should().BeGreaterThan(0);
            }
            finally
            {
                MarkdownViewer.OnFullRender = null;
                window.Close();
            }
        });
    }

    /// <summary>
    /// STEP 1's ordering question, answered by the runtime rather than by
    /// reasoning: for a RECYCLED container, does the Markdown binding update
    /// before or after DataContextChanged fires?
    ///
    /// Before → the DataContextChanged handler's `Content = null` throws away a
    ///          render that already happened synchronously (wasted work AND a
    ///          collapsed row).
    /// After  → the row is measured empty, then fills at Loaded priority (the
    ///          height jump the owner sees).
    /// </summary>
    [Fact]
    public void Step1_BindingVersusDataContextChanged_Ordering()
    {
        RunSta(() =>
        {
            var order = new List<string>();

            var viewer = new MarkdownViewer();
            viewer.DataContextChanged += (_, _) => order.Add("DataContextChanged");

            var descriptor = System.ComponentModel.DependencyPropertyDescriptor.FromProperty(
                MarkdownViewer.MarkdownProperty, typeof(MarkdownViewer));
            descriptor.AddValueChanged(viewer, (_, _) => order.Add("MarkdownBinding"));

            viewer.SetBinding(MarkdownViewer.MarkdownProperty,
                new Binding(nameof(MessageViewModel.RenderContent)));

            viewer.DataContext = new MessageViewModel("a", MessageRole.Assistant, "first", DateTimeOffset.UtcNow);
            Pump();
            order.Clear(); // ignore first attach; the question is about RECYCLE

            viewer.DataContext = new MessageViewModel("b", MessageRole.Assistant, "second", DateTimeOffset.UtcNow);
            Pump();

            order.Should().NotBeEmpty();
            TestOutput.Record("recycle order: " + string.Join(" → ", order));
        });
    }
}

/// <summary>Scratch sink so a measurement survives to the assertion message.
/// xUnit swallows Console output from a worker thread, and these numbers are
/// the point of the run.</summary>
internal static class TestOutput
{
    private static readonly List<string> Lines = new();

    public static void Record(string line)
    {
        lock (Lines) Lines.Add(line);
        System.IO.File.AppendAllText(
            System.IO.Path.Combine(System.IO.Path.GetTempPath(), "sran-scroll-perf.txt"),
            line + Environment.NewLine);
    }
}
