using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>T5 PART B: the build stamp parses the embedded AssemblyInformationalVersion
/// into a compact "build &lt;timestamp&gt; · &lt;hash&gt;" label shown in the UI + logged
/// at startup, so it's visible which binary is running after a publish.</summary>
public sealed class BuildStampTests
{
    [Fact]
    public void Parses_Timestamp_And_Hash_From_A_Full_Informational_Version()
    {
        BuildStamp.Describe("1.0.0+build.2026-07-23 15:50.abc1234")
            .Should().Be("build 2026-07-23 15:50 · abc1234");
    }

    [Fact]
    public void Handles_A_Missing_Git_Hash()
    {
        BuildStamp.Describe("1.0.0+build.2026-07-23 15:50.nogit")
            .Should().Be("build 2026-07-23 15:50 · nogit");
    }

    [Theory]
    [InlineData("1.0.0", "build 1.0.0")]
    [InlineData("", "build unknown")]
    [InlineData(null, "build unknown")]
    public void Falls_Back_Gracefully_Without_A_Build_Segment(string? version, string expected)
    {
        BuildStamp.Describe(version).Should().Be(expected);
    }

    [Fact]
    public void Current_Is_Never_Empty()
    {
        BuildStamp.Current.Should().NotBeNullOrWhiteSpace();
        BuildStamp.Current.Should().StartWith("build ");
    }
}
