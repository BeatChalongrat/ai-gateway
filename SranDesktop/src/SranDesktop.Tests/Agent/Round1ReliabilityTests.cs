using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>R5 (round 1): the pure FailureCode → human-message mapping used to turn a
/// red tool card's raw code into a readable sentence. Only the genuinely-failed
/// (Success=false) codes are mapped; everything else falls back to a generic line.</summary>
public class Round1ReliabilityTests
{
    [Theory]
    [InlineData("FocusLost", "tool.error.focusLost")]
    [InlineData("FocusLost: the expected receiver lost foreground", "tool.error.focusLost")]
    [InlineData("TargetNotFound", "tool.error.targetNotFound")]
    [InlineData("WrongWindow", "tool.error.wrongWindow")]
    [InlineData("UnboundInput", "tool.error.unboundInput")]
    [InlineData("StaleObservation", "tool.error.stale")]
    [InlineData("StaleWindow", "tool.error.stale")]
    [InlineData("CaptureStale", "tool.error.stale")]
    [InlineData("ApplicationNotReady", "tool.error.notReady")]
    [InlineData("ForegroundWindowMismatch", "tool.error.validate")]
    [InlineData("HostWindowTargeted", "tool.error.validate")]
    [InlineData("SomethingUnknown", "tool.error.generic")]
    [InlineData("", "tool.error.generic")]
    [InlineData(null, "tool.error.generic")]
    public void FailureKey_Maps_Red_Codes(string? code, string expected)
        => ToolExecutionItemViewModel.FailureKey(code).Should().Be(expected);

    [Fact]
    public void ExtractFailureCode_Reads_Leading_Token_From_Error_Text()
    {
        var evt = new AgentToolResultEvent(
            "id", "computer_type", "{}", Success: false,
            ResultPreview: "Error: FocusLost: the expected receiver lost foreground.",
            DurationMs: 12,
            FullResult: "Error: FocusLost: the expected receiver lost foreground.");
        ToolExecutionItemViewModel.ExtractFailureCode(evt).Should().Be("FocusLost");
    }

    [Fact]
    public void ExtractFailureCode_Null_When_No_Colon_Delimited_Code()
    {
        var evt = new AgentToolResultEvent(
            "id", "run_command", "{}", Success: false,
            ResultPreview: "boom", DurationMs: 1, FullResult: "boom");
        ToolExecutionItemViewModel.ExtractFailureCode(evt).Should().BeNull();
    }
}
