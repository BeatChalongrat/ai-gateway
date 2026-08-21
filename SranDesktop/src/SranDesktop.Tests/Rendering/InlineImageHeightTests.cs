using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using FluentAssertions;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Scroll-perf STEP 3 — an inline image must not change the row's height when
/// its bitmap becomes ready. The height layout sees first is the height it
/// keeps.
/// </summary>
public class InlineImageHeightTests
{
    // A real 4x3 red PNG (generated, not hand-written — the first hand-written
    // one was not a valid PNG and silently fell back to a link).
    private const string Png4x3 =
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAAXNSR0IArs4c6QAA"
        + "AARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAASSURBVBhXYzghJ/cfGTMQFAAAZAkYJU+j1VsAAAAASUVORK5CYII=";

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

    /// <summary>An inline image lives in an InlineUIContainer inside the
    /// paragraph's TextBlock, not directly under a panel — so the walk has to
    /// descend through Inlines too.</summary>
    private static Image? FindImage(object? node)
    {
        switch (node)
        {
            case null: return null;
            case Image image: return image;
            case System.Windows.Documents.InlineUIContainer container:
                return FindImage(container.Child);
            case TextBlock text:
                foreach (var inline in text.Inlines)
                    if (FindImage(inline) is { } fromText) return fromText;
                return null;
            case System.Windows.Documents.Span span:
                foreach (var inline in span.Inlines)
                    if (FindImage(inline) is { } fromSpan) return fromSpan;
                return null;
            case Panel panel:
                foreach (var child in panel.Children)
                    if (FindImage(child) is { } fromPanel) return fromPanel;
                return null;
            case Border border: return FindImage(border.Child);
            case ContentControl cc: return FindImage(cc.Content);
            case Decorator dec: return FindImage(dec.Child);
            default: return null;
        }
    }

    [Fact]
    public void DataUriImage_HasAnExplicitSizeBeforeLayout()
    {
        RunSta(() =>
        {
            var tree = new MarkdownRenderer().Render($"![chart]({Png4x3})");
            var image = FindImage(tree);
            image.Should().NotBeNull("a data: URI must render as an image, not a link");

            double.IsNaN(image!.Width).Should().BeFalse("an unset Width measures as zero");
            double.IsNaN(image.Height).Should().BeFalse();
            image.Width.Should().BeGreaterThan(0);
            image.Height.Should().BeGreaterThan(0);
        });
    }

    /// <summary>The reserved size must equal what the image actually occupies,
    /// otherwise reserving it would just move the jump rather than remove it.</summary>
    [Fact]
    public void ReservedSize_EqualsTheSizeItActuallyRendersAt()
    {
        RunSta(() =>
        {
            var tree = new MarkdownRenderer().Render($"![chart]({Png4x3})");
            var image = FindImage(tree)!;
            var reserved = new Size(image.Width, image.Height);

            tree.Measure(new Size(768, double.PositiveInfinity));
            tree.Arrange(new Rect(tree.DesiredSize));

            image.ActualWidth.Should().BeApproximately(reserved.Width, 0.5);
            image.ActualHeight.Should().BeApproximately(reserved.Height, 0.5);
        });
    }

    /// <summary>A remote image cannot know its size in time, so it gets a box
    /// that never moves. Nothing may make it resize later.</summary>
    [Fact]
    public void RemoteImage_GetsTheFixedBox()
    {
        RunSta(() =>
        {
            var element = new Image();
            MarkdownRenderer.ReserveInlineImageSize(
                element, BitmapSource.Create(1, 1, 96, 96,
                    System.Windows.Media.PixelFormats.Bgra32, null, new byte[] { 0, 0, 0, 0 }, 4),
                InlineImageKind.Remote);

            element.Width.Should().Be(480);
            element.Height.Should().Be(270);
        });
    }

    /// <summary>The row's height is what actually matters: measuring the same
    /// tree twice must not change it. (A late bitmap would show up here as a
    /// second, larger DesiredSize.)</summary>
    [Fact]
    public void RowHeight_IsStableAcrossRepeatedMeasures()
    {
        RunSta(() =>
        {
            var tree = new MarkdownRenderer().Render(
                $"Some prose above.\n\n![chart]({Png4x3})\n\nAnd prose below.");

            tree.Measure(new Size(768, double.PositiveInfinity));
            var first = tree.DesiredSize.Height;

            tree.InvalidateMeasure();
            tree.Measure(new Size(768, double.PositiveInfinity));
            var second = tree.DesiredSize.Height;

            second.Should().BeApproximately(first, 0.01);
            first.Should().BeGreaterThan(0);
        });
    }

    /// <summary>
    /// Records what DecodePixelWidth actually does to a tiny image. Not a fix —
    /// noted so the report can carry a measured number rather than a suspicion.
    /// </summary>
    [Fact]
    public void DecodeWidth_UpscaleBehaviour_IsRecorded()
    {
        RunSta(() =>
        {
            var tree = new MarkdownRenderer().Render($"![chart]({Png4x3})");
            var image = FindImage(tree)!;
            var source = (BitmapSource)image.Source;
            TestOutput.Record(
                $"4x3 data-uri image: decoded {source.PixelWidth}x{source.PixelHeight}px, " +
                $"reserved {image.Width}x{image.Height} DIP");
        });
    }
}
