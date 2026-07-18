using System.Net;
using FluentAssertions;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Mcp;

/// <summary>web_fetch: URL/scheme validation, the SSRF address block-list, the
/// regex HTML→text extraction and max_length truncation — all pure static
/// logic, no network.</summary>
public class WebFetchToolTests
{
    // ── URL validation ────────────────────────────────────────────────────────

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void ValidateUrl_Rejects_Missing(string? url)
    {
        WebFetchTool.ValidateUrl(url, out var uri).Should().NotBeNull();
        uri.Should().BeNull();
    }

    [Theory]
    [InlineData("ftp://example.com/file")]
    [InlineData("file:///C:/windows/system32")]
    [InlineData("javascript:alert(1)")]
    [InlineData("gopher://example.com")]
    public void ValidateUrl_Rejects_NonHttp_Schemes(string url)
    {
        WebFetchTool.ValidateUrl(url, out var uri).Should().NotBeNull();
        uri.Should().BeNull();
    }

    [Fact]
    public void ValidateUrl_Rejects_Relative()
    {
        WebFetchTool.ValidateUrl("example.com/page", out _).Should().NotBeNull();
    }

    [Theory]
    [InlineData("http://example.com")]
    [InlineData("https://example.com/path?q=1")]
    public void ValidateUrl_Accepts_Http_And_Https(string url)
    {
        WebFetchTool.ValidateUrl(url, out var uri).Should().BeNull();
        uri.Should().NotBeNull();
    }

    // ── SSRF block-list ───────────────────────────────────────────────────────

    [Theory]
    [InlineData("127.0.0.1")]      // loopback
    [InlineData("127.8.8.8")]      // whole 127/8
    [InlineData("10.0.0.5")]       // RFC1918
    [InlineData("172.16.0.1")]     // RFC1918 lower edge
    [InlineData("172.31.255.254")] // RFC1918 upper edge
    [InlineData("192.168.1.1")]    // RFC1918
    [InlineData("169.254.169.254")] // link-local (cloud metadata)
    [InlineData("0.0.0.0")]
    [InlineData("::1")]            // IPv6 loopback
    [InlineData("fc00::1")]        // IPv6 unique local
    [InlineData("fd12:3456::1")]   // fc00::/7 upper half
    [InlineData("fe80::1")]        // IPv6 link-local
    [InlineData("::ffff:127.0.0.1")] // IPv4-mapped loopback
    [InlineData("::ffff:192.168.0.1")] // IPv4-mapped private
    public void IsBlockedAddress_Blocks_Private_Loopback_LinkLocal(string ip)
    {
        WebFetchTool.IsBlockedAddress(IPAddress.Parse(ip)).Should().BeTrue();
    }

    [Theory]
    [InlineData("8.8.8.8")]
    [InlineData("93.184.216.34")]  // example.com
    [InlineData("172.15.0.1")]     // just below 172.16/12
    [InlineData("172.32.0.1")]     // just above 172.16/12
    [InlineData("2606:2800:220:1:248:1893:25c8:1946")] // public IPv6
    public void IsBlockedAddress_Allows_Public(string ip)
    {
        WebFetchTool.IsBlockedAddress(IPAddress.Parse(ip)).Should().BeFalse();
    }

    // ── HTML → text extraction ────────────────────────────────────────────────

    [Fact]
    public void Extract_Takes_Title_And_Strips_Script_Style_Nav_Header_Footer()
    {
        var html = """
            <html><head><title> My  Page </title>
            <style>body { color: red; }</style>
            <script>var secret = "nope";</script></head>
            <body>
            <nav><a href="/home">Home</a></nav>
            <header>Site header</header>
            <p>Real content here.</p>
            <footer>© 2026</footer>
            </body></html>
            """;

        var (title, text) = WebFetchTool.ExtractReadableText(html);

        title.Should().Be("My Page");
        text.Should().Contain("Real content here.");
        text.Should().NotContain("color: red");
        text.Should().NotContain("secret");
        text.Should().NotContain("Site header");
        text.Should().NotContain("© 2026");
        text.Should().NotContain("Home");
    }

    [Fact]
    public void Extract_Removes_Comments_And_All_Tags()
    {
        var (_, text) = WebFetchTool.ExtractReadableText(
            "<body><!-- hidden note --><p>Visible <b>bold</b> text</p></body>");

        text.Should().NotContain("hidden note");
        text.Should().NotContain("<");
        text.Should().Contain("Visible bold text");
    }

    [Fact]
    public void Extract_Decodes_Html_Entities()
    {
        var (_, text) = WebFetchTool.ExtractReadableText(
            "<p>Fish &amp; chips &lt;3 &quot;quoted&quot; &#65;BC</p>");

        text.Should().Contain("Fish & chips <3 \"quoted\"");
        text.Should().Contain("ABC"); // numeric entity decoded
    }

    [Fact]
    public void Extract_Renders_Links_As_Text_With_Url_Only_When_Different()
    {
        var (_, text) = WebFetchTool.ExtractReadableText(
            "<p><a href=\"https://a.example/x\">Docs</a> and " +
            "<a href=\"https://b.example\">https://b.example</a> and " +
            "<a href=\"#top\">Back to top</a></p>");

        text.Should().Contain("Docs (https://a.example/x)");
        text.Should().Contain("https://b.example");
        text.Should().NotContain("https://b.example (https://b.example)");
        text.Should().Contain("Back to top");
        text.Should().NotContain("(#top)");
    }

    [Fact]
    public void Extract_Converts_Block_Elements_To_Newlines_And_Collapses_Runs()
    {
        var (_, text) = WebFetchTool.ExtractReadableText(
            "<div>one</div><div></div><div></div><div></div><p>two</p><br><br><br>three");

        text.Should().Contain("one");
        text.Should().Contain("two");
        text.Should().Contain("three");
        text.Should().NotContain("\n\n\n"); // ≥3 newlines collapsed to 2
    }

    [Fact]
    public void Extract_Handles_Missing_Title()
    {
        var (title, text) = WebFetchTool.ExtractReadableText("<p>no title here</p>");
        title.Should().BeNull();
        text.Should().Be("no title here");
    }

    // ── truncation + clamping ─────────────────────────────────────────────────

    [Fact]
    public void ApplyMaxLength_Passes_Short_Text_Through()
    {
        WebFetchTool.ApplyMaxLength("short", 1000).Should().Be("short");
    }

    [Fact]
    public void ApplyMaxLength_Truncates_With_A_Note()
    {
        var result = WebFetchTool.ApplyMaxLength(new string('a', 5000), 1000);

        result.Should().StartWith(new string('a', 1000));
        result.Should().Contain("[truncated — 1000 of 5000 chars]");
    }

    [Theory]
    [InlineData(0, 1000)]        // below floor
    [InlineData(500, 1000)]      // below floor
    [InlineData(50_000, 50_000)] // default passes through
    [InlineData(999_999, 200_000)] // above ceiling
    public void ClampMaxLength_Clamps_To_Range(int requested, int expected)
    {
        WebFetchTool.ClampMaxLength(requested).Should().Be(expected);
    }
}
