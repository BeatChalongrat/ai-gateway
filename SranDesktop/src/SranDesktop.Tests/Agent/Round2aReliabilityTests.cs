using FluentAssertions;
using SranDesktop.Application.Events;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Round 2a: the transient-failure classifier (single source of truth for
/// auto-retry) and the new retry stream event. The orchestrator auto-retry LOOP that
/// consumes these is staged separately (see SRAN_ROUND2A_RESULT.md).</summary>
public class Round2aReliabilityTests
{
    [Theory]
    [InlineData("FocusLost")]
    [InlineData("TargetNotFound")]
    [InlineData("StaleObservation")]
    [InlineData("StaleWindow")]
    [InlineData("ApplicationNotReady")]
    public void Retryable_Transient_Codes(string code)
        => TransientFailurePolicy.IsRetryable(code).Should().BeTrue(code);

    [Theory]
    [InlineData("UnboundInput")]      // caller mistake — a fresh screenshot won't fix it
    [InlineData("WrongWindow")]       // targeting a logically wrong window
    [InlineData("HostWindowTargeted")]
    [InlineData("EffectMismatch")]    // amber (R3′), never on the red retry path
    [InlineData("ReadBackUnavailable")] // amber (R3′)
    [InlineData("RetryBudgetExceeded")] // runtime's own stop signal
    [InlineData("BlindRetryBlocked")]
    [InlineData("CredentialRefused")]
    [InlineData("")]
    [InlineData(null)]
    public void NonRetryable_Terminal_Codes(string? code)
        => TransientFailurePolicy.IsRetryable(code).Should().BeFalse(code ?? "null");

    [Theory]
    [InlineData("Error: FocusLost: the receiver lost foreground.", "FocusLost")]
    [InlineData("TargetNotFound: target disappeared before dispatch.", "TargetNotFound")]
    [InlineData("ApplicationNotReady: app not input-ready.", "ApplicationNotReady")]
    [InlineData("boom", "boom")]
    public void ExtractCode_Reads_Leading_Token(string text, string expected)
        => TransientFailurePolicy.ExtractCode(text).Should().Be(expected);

    [Theory]
    [InlineData("Error: FocusLost: x", true)]
    [InlineData("Error: TargetNotFound: x", true)]
    [InlineData("Error: UnboundInput: x", false)]
    [InlineData("Error: WrongWindow: x", false)]
    public void IsRetryableError_Classifies_Full_Error_String(string text, bool expected)
        => TransientFailurePolicy.IsRetryableError(text).Should().Be(expected);

    [Fact]
    public void RetryEvent_Carries_Attempt_Max_Reason()
    {
        var evt = new AgentToolRetryEvent("call-1", "computer_type", 2, 5, "FocusLost");
        evt.Attempt.Should().Be(2);
        evt.MaxAttempts.Should().Be(5);
        evt.Reason.Should().Be("FocusLost");
        evt.Should().BeAssignableTo<AgentStreamEvent>("must route through the existing bus");
    }
}
