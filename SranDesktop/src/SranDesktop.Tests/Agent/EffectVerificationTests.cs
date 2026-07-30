using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Agent;

public class EffectVerificationTests
{
    [Fact]
    public void No_State_Change_Does_Not_Verify()
    {
        var expected = new ExpectedEffect(
            ExpectedEffectType.ToggleStateChanged, "microphone", "ToggleState",
            "unmuted", "muted");
        var observed = new EffectObservation(
            "after", expected.Type, expected.Subject, expected.Property,
            "unmuted", "unmuted", true, 1, new[] { "UIA ToggleState=Off" });

        EffectVerifier.Verify(expected, observed).EffectVerified.Should().BeFalse();
    }

    [Fact]
    public void Expected_Toggle_State_Verifies()
    {
        var expected = new ExpectedEffect(
            ExpectedEffectType.ToggleStateChanged, "microphone", "ToggleState",
            "unmuted", "muted");
        var observed = new EffectObservation(
            "after", expected.Type, expected.Subject, expected.Property,
            "unmuted", "muted", true, 1, new[] { "UIA ToggleState=On" });

        EffectVerifier.Verify(expected, observed).EffectVerified.Should().BeTrue();
    }

    [Fact]
    public void OpenApplication_Does_Not_Complete_When_Window_Is_Not_Found()
    {
        var result = OpenApplicationTool.WindowNotFoundResult("Calculator");

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("TaskCompleted=false");
    }
}
