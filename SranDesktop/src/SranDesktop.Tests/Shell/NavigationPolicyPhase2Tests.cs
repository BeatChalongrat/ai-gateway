using FluentAssertions;
using SranDesktop.Shell.Hosting;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// Phase 2 (DESKTOP_SHELL_PLAN.md §2.2): the portal redirect is the re-auth
/// signal, never a permitted navigation. These pin the pure policy decisions
/// behind that: portal recognition classifies REFUSALS only, the portal
/// origin can never be allow-listed, and the one deliberate Phase 2 widening
/// (top-level about:blank, the signed-out blank) is exactly that wide.
/// </summary>
public sealed class NavigationPolicyPhase2Tests
{
    private static NavigationPolicy Policy() =>
        new(new[] { "http://localhost:3002" }, "http://localhost:3001");

    [Theory]
    [InlineData("http://localhost:3001/login?redirect=http%3A%2F%2Flocalhost%3A3002%2Fchat")]
    [InlineData("http://localhost:3001/")]
    [InlineData("http://LOCALHOST:3001/login")]
    public void A_Portal_Navigation_Is_Recognised_And_Still_Refused(string uri)
    {
        var policy = Policy();
        policy.IsTopLevelAllowed(uri).Should().BeFalse("recognition never makes the portal allowed");
        policy.IsPortalNavigation(uri).Should().BeTrue();
    }

    [Theory]
    [InlineData("http://localhost:3002/chat")]     // the page itself
    [InlineData("https://example.com/phishing")]   // an ordinary refusal
    [InlineData("http://localhost:3001x/login")]   // host prefix trick
    [InlineData("http://localhost:30011/login")]   // port prefix trick
    [InlineData("not a uri")]
    public void A_Non_Portal_Navigation_Is_Not_Recognised(string uri)
    {
        Policy().IsPortalNavigation(uri).Should().BeFalse();
    }

    [Fact]
    public void The_Portal_Origin_May_Not_Also_Be_Allow_Listed()
    {
        var act = () => new NavigationPolicy(
            new[] { "http://localhost:3002", "http://localhost:3001" },
            "http://localhost:3001");
        act.Should().Throw<ArgumentException>("an allow-listed portal would let the WebView load the login web app");
    }

    [Fact]
    public void The_Portal_Origin_Must_Be_An_Origin_Not_A_Url()
    {
        var act = () => new NavigationPolicy(new[] { "http://localhost:3002" }, "http://localhost:3001/login");
        act.Should().Throw<ArgumentException>();
    }

    [Fact]
    public void Without_A_Portal_Origin_Nothing_Is_Recognised()
    {
        var policy = new NavigationPolicy(new[] { "http://localhost:3002" });
        policy.IsPortalNavigation("http://localhost:3001/login").Should().BeFalse();
    }

    [Fact]
    public void About_Blank_Is_Allowed_Top_Level_But_Srcdoc_Is_Not()
    {
        // The signed-out blank is the ONLY widening: about:srcdoc stays a
        // frame-only form, and no other about:/scheme sneaks in with it.
        var policy = Policy();
        policy.IsTopLevelAllowed("about:blank").Should().BeTrue();
        policy.IsTopLevelAllowed("about:srcdoc").Should().BeFalse();
        policy.IsTopLevelAllowed("about:blank#x").Should().BeFalse();
        policy.IsFrameAllowed("about:srcdoc").Should().BeTrue();
    }
}
