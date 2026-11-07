using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 7 C2/C6: single-instance pipe wire format (pure part).</summary>
public class SingleInstanceProtocolTests
{
    // ── Encode ──────────────────────────────────────────────────────────────

    [Fact]
    public void No_Args_Encodes_Legacy_Activate()
    {
        SingleInstanceProtocol.Encode(null).Should().Be("activate");
        SingleInstanceProtocol.Encode(Array.Empty<string>()).Should().Be("activate");
        SingleInstanceProtocol.Encode(new[] { "  ", "" }).Should().Be("activate");
    }

    [Fact]
    public void Args_Are_Joined_By_Newline()
    {
        SingleInstanceProtocol
            .Encode(new[] { "sran://oauth/callback?connector=slack&status=ok", "--minimized" })
            .Should().Be("sran://oauth/callback?connector=slack&status=ok\n--minimized");
    }

    // ── Parse ───────────────────────────────────────────────────────────────

    [Fact]
    public void Legacy_Activate_Message_Still_Activates()
    {
        var parsed = SingleInstanceProtocol.Parse("activate");
        parsed.Activate.Should().BeTrue();
        parsed.DeepLink.Should().BeNull();
    }

    [Fact]
    public void Deep_Link_Is_Extracted_And_Window_Still_Activates()
    {
        var parsed = SingleInstanceProtocol.Parse(
            "sran://oauth/callback?connector=slack&status=ok\n--minimized");
        parsed.Activate.Should().BeTrue();
        parsed.DeepLink.Should().Be("sran://oauth/callback?connector=slack&status=ok");
    }

    [Fact]
    public void First_Deep_Link_Wins_When_Several_Are_Smuggled()
    {
        var parsed = SingleInstanceProtocol.Parse(
            "sran://oauth/callback?connector=a&status=ok\nsran://oauth/callback?connector=b&status=ok");
        parsed.DeepLink.Should().Contain("connector=a");
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("some random garbage")]
    public void Garbage_Degrades_To_Plain_Activation(string? message)
    {
        var parsed = SingleInstanceProtocol.Parse(message);
        parsed.Activate.Should().BeTrue();
        parsed.DeepLink.Should().BeNull();
    }

    [Fact]
    public void Oversized_Message_Is_Not_Buffered_As_A_Deep_Link()
    {
        var huge = "sran://oauth/callback?connector=x&status=ok&pad="
                   + new string('a', SingleInstanceProtocol.MaxMessageLength);
        var parsed = SingleInstanceProtocol.Parse(huge);
        parsed.Activate.Should().BeTrue();
        parsed.DeepLink.Should().BeNull();
    }

    [Fact]
    public void Roundtrip_Encode_Then_Parse()
    {
        var msg = SingleInstanceProtocol.Encode(
            new[] { "sran://oauth/callback?connector=github&status=error&reason=provider_denied" });
        var parsed = SingleInstanceProtocol.Parse(msg);
        parsed.DeepLink.Should().Be(
            "sran://oauth/callback?connector=github&status=error&reason=provider_denied");
    }
}
