using FluentAssertions;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Presentation;

/// <summary>FIX-D: well-known turn-error codes map to actionable localized
/// messages; unknown codes keep the raw server message.</summary>
public sealed class TurnErrorLocalizationTests
{
    [Theory]
    [InlineData("insufficient_credits")]
    [InlineData("quota_exceeded")]
    [InlineData("gateway_unreachable")]
    [InlineData("no_agent_key")]
    [InlineData("agent_key_reprovision_failed")]
    [InlineData("authentication_error")]
    [InlineData("stream_stalled")]
    [InlineData("rate_limit_exceeded")]
    [InlineData("context_length_exceeded")]
    public void Known_Codes_Are_Replaced_With_Localized_Text(string code)
    {
        var raw = "Raw upstream message the user should not have to decode";

        var text = ChatViewModel.LocalizeTurnError(code, raw);

        text.Should().NotBe(raw);
        text.Should().NotStartWith("error.").And.NotStartWith("agent.",
            "a missing dictionary key must not leak to the user");
    }

    [Fact]
    public void Unknown_Code_Falls_Back_To_The_Raw_Message()
    {
        ChatViewModel.LocalizeTurnError("upstream_bad_response", "raw text")
            .Should().Be("raw text");
        ChatViewModel.LocalizeTurnError(null, "raw text")
            .Should().Be("raw text");
    }
}
