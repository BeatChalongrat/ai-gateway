using System.Text;
using FluentAssertions;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>Phase C PART 1: inline markdown image render decision + data-URI
/// decode (the WPF Image render itself is a visual check).</summary>
public class InlineImageResolverTests
{
    // A 1x1 transparent PNG.
    private const string PngB64 =
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

    [Fact]
    public void Data_Uri_Image_Is_Decoded_For_Inline_Render()
    {
        var plan = InlineImageResolver.Plan($"data:image/png;base64,{PngB64}");

        plan.Kind.Should().Be(InlineImageKind.DataUri);
        plan.Data.Should().NotBeNull();
        plan.Data!.Length.Should().BeGreaterThan(0);
        // Real PNG signature.
        plan.Data[0].Should().Be(0x89);
    }

    [Theory]
    [InlineData("http://example.com/chart.png")]
    [InlineData("https://example.com/a.jpg")]
    public void Http_Urls_Are_Remote(string url)
    {
        var plan = InlineImageResolver.Plan(url);
        plan.Kind.Should().Be(InlineImageKind.Remote);
        plan.RemoteUrl.Should().Be(url);
        plan.Data.Should().BeNull();
    }

    [Theory]
    [InlineData("file:///C:/secret.png")]           // no local auto-load
    [InlineData("C:\\Users\\me\\secret.png")]
    [InlineData("/etc/passwd")]
    [InlineData("ftp://host/x.png")]
    [InlineData("")]
    [InlineData(null)]
    public void Non_Data_Non_Http_Stays_A_Link(string? url)
    {
        InlineImageResolver.Plan(url).Kind.Should().Be(InlineImageKind.None);
    }

    [Fact]
    public void Malformed_Data_Uri_Falls_Back_To_Link_Without_Throwing()
    {
        InlineImageResolver.Plan("data:image/png;base64,!!!not-base64!!!").Kind
            .Should().Be(InlineImageKind.None);
        InlineImageResolver.Plan("data:image/png,plain-text-not-base64").Kind
            .Should().Be(InlineImageKind.None); // not base64-flagged
    }

    [Fact]
    public void Oversized_Data_Uri_Is_Rejected()
    {
        // A base64 string whose decoded size exceeds the cap → no decode.
        var huge = Convert.ToBase64String(Encoding.ASCII.GetBytes(new string('a', 1)));
        var big = "data:image/png;base64," + new string('A', (InlineImageResolver.MaxDataUriBytes / 3 * 4) + 8);
        InlineImageResolver.TryDecodeDataUri(big, out _).Should().BeFalse();
        _ = huge;
    }

    [Fact]
    public void TryDecode_Returns_Bytes_For_Valid_Base64()
    {
        InlineImageResolver.TryDecodeDataUri($"data:image/png;base64,{PngB64}", out var bytes)
            .Should().BeTrue();
        bytes.Length.Should().BeGreaterThan(0);
    }
}
