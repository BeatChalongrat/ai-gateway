using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 7 C6: strict sran:// deep-link parsing. Any local process
/// can fire these URIs, so hostile shapes must be rejected, not coerced.</summary>
public class OAuthDeepLinkTests
{
    [Fact]
    public void Parses_Success_Callback()
    {
        OAuthDeepLink.TryParse(
            "sran://oauth/callback?connector=slack&status=ok", out var result)
            .Should().BeTrue();
        result!.ConnectorId.Should().Be("slack");
        result.Success.Should().BeTrue();
        result.Reason.Should().BeNull();
    }

    [Fact]
    public void Parses_Error_Callback_With_Reason()
    {
        OAuthDeepLink.TryParse(
            "sran://oauth/callback?connector=gmail&status=error&reason=provider_denied",
            out var result).Should().BeTrue();
        result!.Success.Should().BeFalse();
        result.Reason.Should().Be("provider_denied");
    }

    [Fact]
    public void Scheme_And_Host_Are_Case_Insensitive()
    {
        OAuthDeepLink.TryParse(
            "SRAN://OAUTH/callback?connector=github&status=ok", out var result)
            .Should().BeTrue();
        result!.ConnectorId.Should().Be("github");
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("https://oauth/callback?connector=x&status=ok")]   // wrong scheme
    [InlineData("sran://other/callback?connector=x&status=ok")]    // wrong host
    [InlineData("sran://oauth/steal?connector=x&status=ok")]       // wrong path
    [InlineData("sran://oauth/callback")]                          // no params
    [InlineData("sran://oauth/callback?connector=x")]              // missing status
    [InlineData("sran://oauth/callback?status=ok")]                // missing connector
    [InlineData("sran://oauth/callback?connector=x&status=maybe")] // bad status
    [InlineData("not a uri at all")]
    public void Malformed_Or_Wrong_Target_Is_Rejected(string? uri)
    {
        OAuthDeepLink.TryParse(uri, out var result).Should().BeFalse();
        result.Should().BeNull();
    }

    [Theory]
    [InlineData("../../etc/passwd")]
    [InlineData("x;rm -rf")]
    [InlineData("<script>alert(1)</script>")]
    [InlineData("UPPER_CASE")]
    public void Hostile_Connector_Ids_Are_Rejected(string connector)
    {
        var uri = $"sran://oauth/callback?connector={Uri.EscapeDataString(connector)}&status=ok";
        OAuthDeepLink.TryParse(uri, out _).Should().BeFalse();
    }

    [Fact]
    public void Oversized_Uri_Is_Rejected()
    {
        var uri = "sran://oauth/callback?connector=slack&status=ok&pad="
                  + new string('a', OAuthDeepLink.MaxUriLength);
        OAuthDeepLink.TryParse(uri, out _).Should().BeFalse();
    }

    [Fact]
    public void Hostile_Reason_Is_Dropped_Not_Rejected()
    {
        // Status/connector are trustworthy → parse succeeds; the reason is
        // display-adjacent so a hostile value degrades to null.
        OAuthDeepLink.TryParse(
            "sran://oauth/callback?connector=slack&status=error&reason="
            + Uri.EscapeDataString("<img onerror=x>"),
            out var result).Should().BeTrue();
        result!.Reason.Should().BeNull();
    }

    [Fact]
    public void Duplicate_Params_First_Occurrence_Wins()
    {
        OAuthDeepLink.TryParse(
            "sran://oauth/callback?connector=slack&status=ok&connector=evil",
            out var result).Should().BeTrue();
        result!.ConnectorId.Should().Be("slack");
    }
}
