using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Documents;
using System.Windows.Threading;
using FluentAssertions;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Scroll-perf STEP 1 — a recycled viewer must never blank.
///
/// The invariant, stated so it can be checked without watching frames: at the
/// instant the DataContext swap returns — before ANY dispatcher work runs, so
/// before the virtualizing panel measures the container — Content must already
/// be the NEW message's rendered tree. If that holds, layout can never observe
/// a zero-height row, and the previous message's panel can never be displayed.
/// </summary>
public class MarkdownViewerRecycleTests
{
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

    private static void Pump(DispatcherPriority through = DispatcherPriority.Loaded)
    {
        var frame = new DispatcherFrame();
        Dispatcher.CurrentDispatcher.BeginInvoke(new Action(() => frame.Continue = false), through);
        Dispatcher.PushFrame(frame);
    }

    private static MarkdownViewer BoundViewer(MessageViewModel first)
    {
        var viewer = new MarkdownViewer();
        viewer.SetBinding(MarkdownViewer.MarkdownProperty,
            new Binding(nameof(MessageViewModel.RenderContent)));
        viewer.DataContext = first;
        Pump();
        return viewer;
    }

    private static MessageViewModel Message(string id, string text) =>
        new(id, MessageRole.Assistant, text, DateTimeOffset.UtcNow);

    /// <summary>Concatenates every Run in the rendered tree.</summary>
    private static string TextOf(object? content)
    {
        var sb = new System.Text.StringBuilder();
        Walk(content);
        return sb.ToString();

        void Walk(object? node)
        {
            switch (node)
            {
                case null:
                    return;
                case Run run:
                    sb.Append(run.Text);
                    return;
                case TextBlock tb:
                    foreach (var inline in tb.Inlines) Walk(inline);
                    return;
                case Span span:
                    foreach (var inline in span.Inlines) Walk(inline);
                    return;
                case Panel panel:
                    foreach (var child in panel.Children) Walk(child);
                    return;
                case Border border:
                    Walk(border.Child);
                    return;
                case ContentControl cc:
                    Walk(cc.Content);
                    return;
                case Decorator dec:
                    Walk(dec.Child);
                    return;
            }
        }
    }

    [Fact]
    public void RecycledViewer_HasTheNewContent_BeforeAnyDispatcherWork()
    {
        RunSta(() =>
        {
            var viewer = BoundViewer(Message("a", "first message body"));
            TextOf(viewer.Content).Should().Contain("first message body");

            viewer.DataContext = Message("b", "second message body");

            // Deliberately NO Pump: this is the state the generation pass — and
            // therefore Measure — sees.
            viewer.Content.Should().NotBeNull("a blank row is what collapses the height");
            TextOf(viewer.Content).Should().Contain("second message body");
            TextOf(viewer.Content).Should().NotContain("first message body");
        });
    }

    /// <summary>The case the old Loaded-priority fallback existed for: when the
    /// new message's text EQUALS the old, the Markdown DP never changes, so no
    /// callback fires. It must still be correct and still never blank.</summary>
    [Fact]
    public void RecycledViewer_WithIdenticalMarkdown_StaysRendered()
    {
        RunSta(() =>
        {
            const string same = "identical body text";
            var viewer = BoundViewer(Message("a", same));
            var before = viewer.Content;
            before.Should().NotBeNull();

            viewer.DataContext = Message("b", same);

            viewer.Content.Should().NotBeNull();
            TextOf(viewer.Content).Should().Contain(same);
        });
    }

    /// <summary>A viewer recycled onto a STREAMING message keeps the original
    /// behaviour — empty until the first stream tick, with the typing indicator
    /// covering the gap. Step 1 must not change that.</summary>
    [Fact]
    public void RecycledViewer_OntoStreamingMessage_StillClears()
    {
        RunSta(() =>
        {
            var viewer = BoundViewer(Message("a", "done text"));

            var streaming = Message("b", "");
            streaming.IsStreaming = true;
            viewer.SetBinding(MarkdownViewer.IsStreamingProperty,
                new Binding(nameof(MessageViewModel.IsStreaming)));
            viewer.DataContext = streaming;

            TextOf(viewer.Content).Should().NotContain("done text");
        });
    }

    /// <summary>
    /// The ordering the whole recycle fix rests on, kept as a live check rather
    /// than a comment: on a RECYCLED container DataContextChanged fires BEFORE
    /// the Markdown binding delivers — and the binding delivers synchronously,
    /// within the same DataContext assignment.
    ///
    /// That is why the viewer cannot render the new message when
    /// DataContextChanged fires (Markdown still holds the previous text) and
    /// why it does not need to blank while waiting (the new text arrives before
    /// the container is measured). If this ever reverses, MarkdownViewer's
    /// _pendingSwap handshake is wrong and these tests should start failing
    /// together.
    /// </summary>
    [Fact]
    public void DataContextChanged_FiresBeforeTheMarkdownBindingDelivers()
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
            viewer.DataContext = Message("a", "first");
            Pump();
            order.Clear(); // first attach is not a recycle

            viewer.DataContext = Message("b", "second");

            // No Pump: both must already have happened.
            order.Should().Equal("DataContextChanged", "MarkdownBinding");
        });
    }

    /// <summary>A viewer whose Markdown is a literal does not depend on the
    /// DataContext at all, so a DataContext change must leave it alone. The old
    /// code blanked it and then re-rendered the same literal — a wasted render
    /// and a blank frame for content that had not changed.</summary>
    [Fact]
    public void UnboundViewer_KeepsItsLiteralContent()
    {
        RunSta(() =>
        {
            var viewer = new MarkdownViewer { Markdown = "literal text" };
            Pump();
            TextOf(viewer.Content).Should().Contain("literal text");

            viewer.DataContext = Message("b", "irrelevant");
            Pump();

            viewer.Content.Should().NotBeNull();
            TextOf(viewer.Content).Should().Contain("literal text");
            TextOf(viewer.Content).Should().NotContain("irrelevant");
        });
    }
}
