using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Documents;
using System.Windows.Threading;
using FluentAssertions;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.Rendering;

using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Scroll-perf STEP 2 — the render cache, and the four ways it must MISS.
/// A cache that serves a stale tree after a theme or language switch is worse
/// than the jank it replaces, so each invalidation gets its own test.
/// </summary>
[Collection("RenderCache")] // shares one static cache — must not run in parallel
public class RenderedMessageCacheTests
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

    /// <summary>Pump until <paramref name="done"/> or the timeout — for paths
    /// that go through a DispatcherTimer and so need wall-clock, not just a
    /// drained queue.</summary>
    private static void PumpUntil(Func<bool> done, int timeoutMs = 2000)
    {
        var deadline = Environment.TickCount64 + timeoutMs;
        while (!done() && Environment.TickCount64 < deadline)
        {
            Pump(DispatcherPriority.SystemIdle);
            Thread.Sleep(10);
        }
    }

    private static MessageViewModel Message(string id, string text) =>
        new(id, MessageRole.Assistant, text, DateTimeOffset.UtcNow);

    /// <summary>One viewer standing in for a recycled container: swap its
    /// DataContext, pump, and report the tree it ended up showing.</summary>
    private sealed class Viewer
    {
        public readonly MarkdownViewer Control = new();

        public Viewer()
        {
            Control.SetBinding(MarkdownViewer.MarkdownProperty,
                new Binding(nameof(MessageViewModel.RenderContent)));
        }

        public object? Show(MessageViewModel message)
        {
            Control.DataContext = message;
            Pump();
            return Control.Content;
        }
    }

    // ── hit ────────────────────────────────────────────────────────────────

    [Fact]
    public void SameMessageRecycledBack_ReusesTheSameTree()
    {
        RunSta(() =>
        {
            MarkdownViewer.ClearRenderCache();
            var a = Message("a", "the first message body");
            var b = Message("b", "an entirely different body");

            var viewer = new Viewer();
            var firstTree = viewer.Show(a);
            viewer.Show(b);                       // recycled away
            var secondTree = viewer.Show(a);      // scrolled back

            secondTree.Should().BeSameAs(firstTree,
                "a message scrolled back into view must be re-attached, not re-parsed");
        });
    }

    // ── the four misses ────────────────────────────────────────────────────

    [Fact]
    public void EditedContent_Misses()
    {
        RunSta(() =>
        {
            MarkdownViewer.ClearRenderCache();
            var message = Message("a", "before the edit");
            var viewer = new Viewer();
            var first = viewer.Show(message);

            message.Content = "after the edit";

            // An edit to an ALREADY rendered message goes through the viewer's
            // pre-existing 66 ms coalescing timer, not the synchronous swap
            // path — so this waits for wall-clock, not just for the queue.
            PumpUntil(() => !ReferenceEquals(viewer.Control.Content, first));

            viewer.Control.Content.Should().NotBeSameAs(first);
            TextOf(viewer.Control.Content).Should().Contain("after the edit");
            TextOf(viewer.Control.Content).Should().NotContain("before the edit");
        });
    }

    /// <summary>
    /// Tested at the key rather than end-to-end: ThemeManager.Apply writes into
    /// Application.Current.Resources, and a unit test has no Application. The
    /// key is the stronger guarantee anyway — it makes a cross-theme hit
    /// impossible even if the ThemeChanged clear never ran.
    /// </summary>
    [Fact]
    public void ThemeIsPartOfTheKey_SoACrossThemeHitCannotHappen()
    {
        RunSta(() =>
        {
            var cache = new RenderedMessageCache();
            var owner = new object();
            var dark = new RenderedMessageCache.Key(owner, "body", "en", Dark: true);
            var light = new RenderedMessageCache.Key(owner, "body", "en", Dark: false);

            cache.Put(dark, new Border());

            cache.TryGet(light, new ContentControl(), out _).Should().BeFalse();
            cache.TryGet(dark, new ContentControl(), out _).Should().BeTrue();
        });
    }

    [Fact]
    public void LocaleChange_Misses()
    {
        RunSta(() =>
        {
            MarkdownViewer.ClearRenderCache();
            var previous = SranDesktop.Presentation.Localization.LocaleService.Instance.Locale;
            try
            {
                var message = Message("a", "body text");
                var viewer = new Viewer();
                var first = viewer.Show(message);
                viewer.Show(Message("b", "other"));

                SranDesktop.Presentation.Localization.LocaleService.Instance.Locale =
                    previous == "th" ? "en" : "th";
                Pump();

                viewer.Show(message).Should().NotBeSameAs(first,
                    "MarkdownRenderer bakes translated strings into the tree");
            }
            finally
            {
                SranDesktop.Presentation.Localization.LocaleService.Instance.Locale = previous;
            }
        });
    }

    [Fact]
    public void EvictsTheLeastRecentlyUsed_AtCapacity()
    {
        RunSta(() =>
        {
            var cache = new RenderedMessageCache(capacity: 3);
            var owners = Enumerable.Range(0, 4).Select(_ => new object()).ToArray();
            var keys = owners
                .Select(o => new RenderedMessageCache.Key(o, "same body", "en", true))
                .ToArray();

            for (var i = 0; i < 3; i++)
                cache.Put(keys[i], new Border());
            cache.Count.Should().Be(3);

            // Touch 0 so 1 becomes the least recently used.
            cache.TryGet(keys[0], new Border(), out _).Should().BeTrue();

            cache.Put(keys[3], new Border());

            cache.Count.Should().Be(3, "capacity is a hard cap");
            cache.TryGet(keys[1], new Border(), out _).Should().BeFalse("1 was the LRU");
            cache.TryGet(keys[0], new Border(), out _).Should().BeTrue();
            cache.TryGet(keys[3], new Border(), out _).Should().BeTrue();
        });
    }

    // ── the one-parent rule ────────────────────────────────────────────────

    [Fact]
    public void ElementStillOwnedByAnotherViewer_IsAMiss()
    {
        RunSta(() =>
        {
            var cache = new RenderedMessageCache();
            var owner = new object();
            var key = new RenderedMessageCache.Key(owner, "body", "en", true);

            var element = new Border();
            var holder = new ContentControl { Content = element }; // element.Parent = holder
            cache.Put(key, element);

            cache.TryGet(key, new ContentControl(), out _).Should().BeFalse(
                "handing a parented element to a second viewer would throw");

            // And the unusable entry is dropped rather than retried forever.
            holder.Content = null;
            cache.TryGet(key, new ContentControl(), out _).Should().BeFalse();
        });
    }

    [Fact]
    public void TheSameViewerAskingAgain_IsAHit()
    {
        RunSta(() =>
        {
            var cache = new RenderedMessageCache();
            var owner = new object();
            var key = new RenderedMessageCache.Key(owner, "body", "en", true);

            var element = new Border();
            var viewer = new ContentControl { Content = element };
            cache.Put(key, element);

            cache.TryGet(key, viewer, out var got).Should().BeTrue();
            got.Should().BeSameAs(element);
        });
    }

    // ── streaming must not go through the cache ────────────────────────────

    /// <summary>
    /// A streaming message renders incrementally and is never cached — its text
    /// changes on every tick, so an entry would be garbage the moment it was
    /// written. When the turn ends the authoritative full render still fires.
    /// </summary>
    [Fact]
    public void StreamingStillRendersProgressively_AndTheFinalRenderStillFires()
    {
        RunSta(() =>
        {
            MarkdownViewer.ClearRenderCache();

            var message = Message("a", "");
            message.IsStreaming = true;

            var viewer = new MarkdownViewer();
            viewer.SetBinding(MarkdownViewer.IsStreamingProperty,
                new Binding(nameof(MessageViewModel.IsStreaming)));
            viewer.SetBinding(MarkdownViewer.MarkdownProperty,
                new Binding(nameof(MessageViewModel.RenderContent)));
            viewer.DataContext = message;
            Pump();

            message.AppendContent("partial answer text");
            PumpUntil(() => TextOf(viewer.Content).Contains("partial answer text"));
            TextOf(viewer.Content).Should().Contain("partial answer text",
                "the incremental path must still run while streaming");

            var cachedDuringStream = MarkdownViewer.CachedTreeCount;
            cachedDuringStream.Should().Be(0,
                "a streaming message's text changes every tick — caching it would " +
                "store something stale on arrival");

            var streamingTree = viewer.Content;
            message.AppendContent(" and its conclusion");
            message.IsStreaming = false;
            Pump();

            viewer.Content.Should().NotBeSameAs(streamingTree,
                "streaming end must still do one authoritative full render");
            TextOf(viewer.Content).Should().Contain("and its conclusion");
            MarkdownViewer.CachedTreeCount.Should().Be(1,
                "and that final tree is what gets cached");
        });
    }

    // ── memory ─────────────────────────────────────────────────────────────

    /// <summary>Not an assertion about a budget — it records what a full cache
    /// costs so the number in the report is measured rather than guessed.</summary>
    [Fact]
    public void MemoryCostAtCapacity_IsRecorded()
    {
        RunSta(() =>
        {
            const int Capacity = 60;
            var renderer = new MarkdownRenderer();
            var bodies = Enumerable.Range(0, Capacity).Select(i =>
                $"### Heading {i}\n\n"
                + "A paragraph of assistant prose that wraps over several lines in the "
                + "768 pixel column, which is what a typical message looks like.\n\n"
                + "- first bullet\n- second bullet\n- third bullet\n\n"
                + $"```csharp\nvar x{i} = Compute({i});\n// a comment\n```\n\n"
                + "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n").ToArray();

            GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect();
            var before = GC.GetTotalMemory(true);

            var cache = new RenderedMessageCache(Capacity);
            for (var i = 0; i < Capacity; i++)
                cache.Put(new RenderedMessageCache.Key(new object(), bodies[i], "en", true),
                    renderer.Render(bodies[i]));

            GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect();
            var after = GC.GetTotalMemory(true);

            cache.Count.Should().Be(Capacity);
            GC.KeepAlive(cache);

            var bytes = after - before;
            TestOutput.Record(
                $"cache memory: {Capacity} trees = {bytes / 1024.0:F0} KB " +
                $"({bytes / (double)Capacity / 1024.0:F1} KB per message)");
        });
    }

    private static string TextOf(object? content)
    {
        var sb = new System.Text.StringBuilder();
        Walk(content);
        return sb.ToString();

        void Walk(object? node)
        {
            switch (node)
            {
                case null: return;
                case Run run: sb.Append(run.Text); return;
                case TextBlock tb:
                    foreach (var i in tb.Inlines) Walk(i);
                    return;
                case Span span:
                    foreach (var i in span.Inlines) Walk(i);
                    return;
                case Panel panel:
                    foreach (var c in panel.Children) Walk(c);
                    return;
                case Border border: Walk(border.Child); return;
                case ContentControl cc: Walk(cc.Content); return;
                case Decorator dec: Walk(dec.Child); return;
            }
        }
    }
}

[CollectionDefinition("RenderCache", DisableParallelization = true)]
public class RenderCacheCollection { }
