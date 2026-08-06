using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 13 Task 3 — per-conversation scroll memory (web P7 parity).
///
/// The rule that matters most is the one that says "don't restore": a
/// remembered position that WAS the bottom must not be replayed as a fixed
/// offset, because that would silently unpin the view and stop a live stream
/// from following — a much worse bug than landing at the bottom.
/// </summary>
public sealed class ScrollMemoryTests
{
    [Fact]
    public void Remembers_A_Position_Per_Conversation()
    {
        var memory = new ScrollMemory();

        memory.Remember("a", 120);
        memory.Remember("b", 340);

        memory.Recall("a").Should().Be(120);
        memory.Recall("b").Should().Be(340);
    }

    [Fact]
    public void An_Unseen_Conversation_Has_No_Position()
    {
        new ScrollMemory().Recall("never-opened").Should().BeNull(
            "null is what tells the caller to keep the default bottom");
    }

    [Fact]
    public void Ignores_A_Missing_Conversation_Id()
    {
        var memory = new ScrollMemory();

        memory.Remember(null, 10);
        memory.Remember("", 10);

        memory.Count.Should().Be(0);
        memory.Recall(null).Should().BeNull();
    }

    [Fact]
    public void Negative_Offsets_Are_Clamped()
    {
        var memory = new ScrollMemory();

        memory.Remember("a", -50);

        memory.Recall("a").Should().Be(0);
    }

    [Fact]
    public void Forgetting_Returns_A_Chat_To_The_Default()
    {
        var memory = new ScrollMemory();
        memory.Remember("a", 99);

        memory.Forget("a");

        memory.Recall("a").Should().BeNull();
        memory.Count.Should().Be(0);
    }

    [Fact]
    public void Evicts_The_Least_Recently_Touched_Beyond_The_Cap()
    {
        var memory = new ScrollMemory();
        for (var i = 0; i < ScrollMemory.MaxRemembered; i++)
            memory.Remember($"c{i}", i);

        // Touch the oldest so it is no longer the eviction candidate.
        memory.Remember("c0", 7);
        memory.Remember("overflow", 1);

        memory.Count.Should().Be(ScrollMemory.MaxRemembered);
        memory.Recall("c0").Should().Be(7, "it was re-touched");
        memory.Recall("c1").Should().BeNull("it became the oldest");
        memory.Recall("overflow").Should().Be(1);
    }

    [Fact]
    public void Re_Remembering_Updates_Rather_Than_Duplicates()
    {
        var memory = new ScrollMemory();

        memory.Remember("a", 10);
        memory.Remember("a", 20);

        memory.Count.Should().Be(1);
        memory.Recall("a").Should().Be(20);
    }
}

/// <summary>What a remembered offset means for a freshly rendered list.</summary>
public sealed class ScrollRestoreTests
{
    private const double Threshold = 4.0;

    [Fact]
    public void Restores_A_Mid_Thread_Position()
    {
        ScrollRestore.Resolve(saved: 300, scrollableHeight: 1000, Threshold)
            .Should().Be(300);
    }

    [Fact]
    public void Nothing_Saved_Means_Keep_The_Default_Bottom()
    {
        ScrollRestore.Resolve(saved: null, scrollableHeight: 1000, Threshold)
            .Should().BeNull();
    }

    [Fact]
    public void A_Bottom_Position_Is_Not_Replayed_As_A_Fixed_Offset()
    {
        // Otherwise reopening a chat you had left at the bottom would pin the
        // view to yesterday's pixel and a new stream would scroll away unseen.
        ScrollRestore.Resolve(saved: 1000, scrollableHeight: 1000, Threshold)
            .Should().BeNull();
        ScrollRestore.Resolve(saved: 998, scrollableHeight: 1000, Threshold)
            .Should().BeNull("within the bottom threshold counts as the bottom");
    }

    [Fact]
    public void A_Shorter_Thread_Clamps_Instead_Of_Scrolling_Past_The_End()
    {
        // Messages deleted, or a wider window: the old offset overshoots.
        // Clamping lands at the bottom, which Resolve then treats as "default".
        ScrollRestore.Resolve(saved: 5000, scrollableHeight: 400, Threshold)
            .Should().BeNull();
        ScrollRestore.Resolve(saved: 350, scrollableHeight: 400, Threshold)
            .Should().Be(350);
    }

    [Fact]
    public void Content_Too_Short_To_Scroll_Has_Nothing_To_Restore()
    {
        ScrollRestore.Resolve(saved: 120, scrollableHeight: 0, Threshold)
            .Should().BeNull();
    }
}
