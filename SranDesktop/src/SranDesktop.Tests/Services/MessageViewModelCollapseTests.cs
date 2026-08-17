using System.ComponentModel;
using FluentAssertions;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// P2 — long user-message collapse, VM state machine. The view reports the
/// RENDERED text height (wrapped line-boxes, measured with FormattedText —
/// never a "\n" count) through ReportUserTextMetrics; everything the XAML
/// binds to (CanCollapse / IsExpanded / UserTextMaxHeight) is decided here so
/// recycled ListBox containers keep the state. The actual measurement needs a
/// live visual tree and is NOT testable headless — covered by manual QA.
/// </summary>
public class MessageViewModelCollapseTests
{
    private const double Line = 24; // the pill's LineHeight in MessageBubble.xaml

    private static MessageViewModel UserMessage() =>
        new("m1", MessageRole.User, "text", DateTimeOffset.UtcNow);

    [Fact]
    public void Defaults_NoCollapse_NoCap()
    {
        var vm = UserMessage();

        vm.CanCollapse.Should().BeFalse();
        vm.IsExpanded.Should().BeFalse();
        vm.UserTextMaxHeight.Should().Be(double.PositiveInfinity,
            "nothing may clip before a real measurement");
    }

    [Theory]
    [InlineData(10)]
    [InlineData(11)]
    [InlineData(12)] // exactly the threshold renders as today — no toggle
    public void AtOrUnderThreshold_DoesNotCollapse(int lines)
    {
        var vm = UserMessage();

        vm.ReportUserTextMetrics(fullTextHeight: lines * Line, lineHeight: Line);

        vm.CanCollapse.Should().BeFalse();
        vm.UserTextMaxHeight.Should().Be(double.PositiveInfinity);
    }

    [Theory]
    [InlineData(13)]
    [InlineData(40)]
    public void PastThreshold_CollapsesToEightLines(int lines)
    {
        var vm = UserMessage();

        vm.ReportUserTextMetrics(fullTextHeight: lines * Line, lineHeight: Line);

        vm.CanCollapse.Should().BeTrue();
        vm.IsExpanded.Should().BeFalse("collapsed is the default on every load");
        vm.UserTextMaxHeight.Should().Be(MessageViewModel.CollapseVisibleLines * Line);
    }

    [Fact]
    public void AssistantMessages_NeverCollapse()
    {
        var vm = new MessageViewModel("m2", MessageRole.Assistant, "text", DateTimeOffset.UtcNow);

        vm.ReportUserTextMetrics(fullTextHeight: 100 * Line, lineHeight: Line);

        vm.CanCollapse.Should().BeFalse();
        vm.UserTextMaxHeight.Should().Be(double.PositiveInfinity);
    }

    [Fact]
    public void ExpandCollapse_TogglesTheCap_AndRaisesPropertyChanged()
    {
        var vm = UserMessage();
        vm.ReportUserTextMetrics(fullTextHeight: 40 * Line, lineHeight: Line);
        var raised = new List<string?>();
        vm.PropertyChanged += (_, e) => raised.Add(e.PropertyName);

        vm.IsExpanded = true;
        vm.UserTextMaxHeight.Should().Be(double.PositiveInfinity);

        vm.IsExpanded = false;
        vm.UserTextMaxHeight.Should().Be(MessageViewModel.CollapseVisibleLines * Line);

        raised.Should().Contain(nameof(MessageViewModel.IsExpanded));
        raised.Should().Contain(nameof(MessageViewModel.UserTextMaxHeight),
            "the XAML MaxHeight binding must see every toggle");
    }

    [Fact]
    public void WidthChange_ReMeasure_CanClearTheCollapse()
    {
        // Narrow window: 40 wrapped lines → collapses. Widened window: the
        // same text wraps to 5 lines → the toggle must disappear.
        var vm = UserMessage();
        vm.ReportUserTextMetrics(fullTextHeight: 40 * Line, lineHeight: Line);
        vm.CanCollapse.Should().BeTrue();

        vm.ReportUserTextMetrics(fullTextHeight: 5 * Line, lineHeight: Line);

        vm.CanCollapse.Should().BeFalse();
        vm.UserTextMaxHeight.Should().Be(double.PositiveInfinity);
    }

    [Fact]
    public void SearchHit_ExpandsACollapsedMessage()
    {
        var vm = UserMessage();
        vm.ReportUserTextMetrics(fullTextHeight: 40 * Line, lineHeight: Line);

        vm.ExpandForSearchHit();

        vm.IsExpanded.Should().BeTrue();
        vm.UserTextMaxHeight.Should().Be(double.PositiveInfinity);
    }

    [Fact]
    public void SearchHit_OnAShortMessage_IsANoOp()
    {
        var vm = UserMessage();
        vm.ReportUserTextMetrics(fullTextHeight: 3 * Line, lineHeight: Line);

        vm.ExpandForSearchHit();

        vm.IsExpanded.Should().BeFalse();
    }

    [Fact]
    public void InvalidMetrics_AreIgnored()
    {
        var vm = UserMessage();

        vm.ReportUserTextMetrics(fullTextHeight: 40 * Line, lineHeight: 0);

        vm.CanCollapse.Should().BeFalse();
    }
}
