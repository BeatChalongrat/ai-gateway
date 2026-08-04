using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// BUG 2: a new chat whose attachment was still uploading had no stored
/// messages, so the empty-chat cleaner deleted it mid-flight and the reply
/// landed nowhere. This tracker is the second signal the cleaner consults;
/// its counting has to survive overlapping uploads and double-dispose or the
/// hole reopens.
/// </summary>
public sealed class ConversationActivityTrackerTests
{
    [Fact]
    public void An_Untouched_Conversation_Is_Not_Busy()
    {
        var tracker = new ConversationActivityTracker();

        tracker.IsBusy("conv-1").Should().BeFalse();
    }

    [Fact]
    public void A_Conversation_Is_Busy_For_The_Life_Of_The_Scope()
    {
        var tracker = new ConversationActivityTracker();

        using (tracker.Begin("conv-1"))
        {
            tracker.IsBusy("conv-1").Should().BeTrue();
        }

        tracker.IsBusy("conv-1").Should().BeFalse();
    }

    [Fact]
    public void Overlapping_Work_Keeps_It_Busy_Until_The_Last_One_Finishes()
    {
        // Two attachments upload concurrently. The first to finish must not
        // clear the flag the second still needs — that would put the
        // conversation back on the chopping block mid-upload.
        var tracker = new ConversationActivityTracker();

        var first = tracker.Begin("conv-1");
        var second = tracker.Begin("conv-1");

        first.Dispose();
        tracker.IsBusy("conv-1").Should().BeTrue("the second upload is still running");

        second.Dispose();
        tracker.IsBusy("conv-1").Should().BeFalse();
    }

    [Fact]
    public void Disposing_Twice_Does_Not_Release_Someone_Elses_Work()
    {
        var tracker = new ConversationActivityTracker();

        var first = tracker.Begin("conv-1");
        var second = tracker.Begin("conv-1");

        first.Dispose();
        first.Dispose(); // a stray second dispose must be a no-op

        tracker.IsBusy("conv-1").Should().BeTrue();
        second.Dispose();
        tracker.IsBusy("conv-1").Should().BeFalse();
    }

    [Fact]
    public void Conversations_Are_Tracked_Independently()
    {
        var tracker = new ConversationActivityTracker();

        using (tracker.Begin("conv-1"))
        {
            tracker.IsBusy("conv-1").Should().BeTrue();
            tracker.IsBusy("conv-2").Should().BeFalse();
        }
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void A_Missing_Id_Is_Never_Busy_And_Never_Throws(string? id)
    {
        var tracker = new ConversationActivityTracker();

        using var scope = tracker.Begin(id!);

        tracker.IsBusy(id!).Should().BeFalse();
    }

    [Fact]
    public void Concurrent_Begin_And_Dispose_Settle_At_Not_Busy()
    {
        // Uploads finish on threadpool threads; the counter must not drift.
        var tracker = new ConversationActivityTracker();

        Parallel.For(0, 200, _ =>
        {
            using var scope = tracker.Begin("conv-1");
        });

        tracker.IsBusy("conv-1").Should().BeFalse();
    }
}
