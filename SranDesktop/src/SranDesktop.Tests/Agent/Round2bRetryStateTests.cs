using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Round 2b: the tool card's "retrying" state — repeated auto-retries collapse
/// into one card showing an amber "Retrying n/m — reason" line instead of a stack of red
/// X's, and the terminal state cleanly replaces it. (Visual binding is covered by the STA
/// gate in SranDesktop.UiTests.)</summary>
public class Round2bRetryStateTests
{
    public Round2bRetryStateTests() => LocaleService.Instance.Locale = "en";

    [Fact]
    public void BeginRetry_Sets_Retrying_State_And_Hint()
    {
        var card = new ToolExecutionItemViewModel("c1", "computer_type", "{}");
        card.BeginRetry(2, 3, "FocusLost");

        card.IsRetrying.Should().BeTrue();
        card.IsFailed.Should().BeFalse("retrying must not show a red X");
        card.IsOk.Should().BeFalse();
        card.RetryHint.Should().Be("Retrying 2/3 — lost focus");
    }

    [Fact]
    public void Terminal_Complete_Clears_RetryHint()
    {
        var card = new ToolExecutionItemViewModel("c1", "computer_type", "{}");
        card.BeginRetry(1, 3, "FocusLost");

        card.Complete(new AgentToolResultEvent(
            "c1", "computer_type", "{}", Success: false,
            ResultPreview: "Error: FocusLost: lost foreground", DurationMs: 3,
            FullResult: "Error: FocusLost: lost foreground"));

        card.IsRetrying.Should().BeFalse();
        card.RetryHint.Should().BeNull();
        card.IsFailed.Should().BeTrue();
        card.HumanError.Should().NotBeNullOrWhiteSpace("R5 human-error still applies after retries exhaust");
    }

    [Fact]
    public void Retry_Then_Verified_Ends_Green_Without_Hint()
    {
        var card = new ToolExecutionItemViewModel("c1", "read_file", "{}");
        card.BeginRetry(1, 3, "FocusLost");
        card.Complete(new AgentToolResultEvent(
            "c1", "read_file", "{}", Success: true, ResultPreview: "ok", DurationMs: 1,
            FullResult: "ok"), requireVerification: false);

        card.IsOk.Should().BeTrue();
        card.IsRetrying.Should().BeFalse();
        card.RetryHint.Should().BeNull();
    }

    [Theory]
    [InlineData("FocusLost", "lost focus")]
    [InlineData("TargetNotFound", "target not found")]
    [InlineData("StaleObservation", "screen changed")]
    [InlineData("StaleWindow", "screen changed")]
    [InlineData("ApplicationNotReady", "app not ready")]
    [InlineData("SomethingElse", "transient issue")]
    public void RetryReason_Maps_Codes(string code, string expected)
        => ToolExecutionItemViewModel.RetryReason(code).Should().Be(expected);
}
