using FluentAssertions;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

public class ArtifactSandboxTests
{
    // ── CSP injection ──────────────────────────────────────────────────────

    [Fact]
    public void Injects_After_Head_When_Present()
    {
        var html = "<html><head><title>x</title></head><body>y</body></html>";

        var result = ArtifactSandbox.InjectCsp(html);

        result.Should().Contain("Content-Security-Policy");
        result.IndexOf("Content-Security-Policy", StringComparison.Ordinal)
            .Should().BeLessThan(result.IndexOf("<title>", StringComparison.Ordinal));
        result.Should().Contain("<body>y</body>");
    }

    [Fact]
    public void Synthesizes_Head_When_Html_Has_None()
    {
        var html = "<html><body>y</body></html>";

        var result = ArtifactSandbox.InjectCsp(html);

        result.Should().Contain("<head>");
        result.Should().Contain("Content-Security-Policy");
        result.Should().Contain("<body>y</body>");
    }

    [Fact]
    public void Prepends_To_A_Bare_Fragment()
    {
        var result = ArtifactSandbox.InjectCsp("<h1>hi</h1>");

        result.Should().StartWith("<meta http-equiv=\"Content-Security-Policy\"");
        result.Should().EndWith("<h1>hi</h1>");
    }

    [Fact]
    public void Is_Idempotent_On_Our_Exact_Meta()
    {
        var once = ArtifactSandbox.InjectCsp("<html><head></head><body>x</body></html>");
        var twice = ArtifactSandbox.InjectCsp(once);

        twice.Should().Be(once);
        // exactly one of OUR metas
        CountOccurrences(twice, ArtifactSandbox.CspMetaTag()).Should().Be(1);
    }

    [Fact]
    public void Foreign_Permissive_Csp_Does_Not_Suppress_Ours()
    {
        // Attacker ships their own loose CSP — ours must still be injected;
        // the browser enforces the intersection so the loose one can't relax it.
        var attacker = "<html><head>" +
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src *; connect-src *\">" +
            "</head><body>x</body></html>";

        var result = ArtifactSandbox.InjectCsp(attacker);

        result.Should().Contain(ArtifactSandbox.CspMetaTag());   // ours, verbatim
        result.Should().Contain("default-src *; connect-src *"); // theirs still there
        result.Should().Contain("connect-src 'none'");           // ours locks connect
    }

    [Fact]
    public void Body_Text_Mentioning_Csp_Still_Gets_Injection()
    {
        // A code sample / article that merely NAMES the header is not a meta.
        var html = "<html><head></head><body>This page explains Content-Security-Policy.</body></html>";

        var result = ArtifactSandbox.InjectCsp(html);

        CountOccurrences(result, ArtifactSandbox.CspMetaTag()).Should().Be(1);
    }

    private static int CountOccurrences(string haystack, string needle)
    {
        var count = 0;
        var i = 0;
        while ((i = haystack.IndexOf(needle, i, StringComparison.Ordinal)) >= 0)
        {
            count++;
            i += needle.Length;
        }
        return count;
    }

    [Fact]
    public void Csp_Blocks_Network_And_Default_Src()
    {
        ArtifactSandbox.ContentSecurityPolicy.Should().Contain("default-src 'none'");
        ArtifactSandbox.ContentSecurityPolicy.Should().Contain("connect-src 'none'");
    }

    // ── Navigation predicate ───────────────────────────────────────────────

    [Theory]
    [InlineData("")]
    [InlineData("about:blank")]
    [InlineData("data:text/html,<h1>x</h1>")]
    [InlineData("https://appassets.sran/react.production.min.js")]
    [InlineData("https://APPASSETS.SRAN/x.js")]
    public void Allowed_Navigations(string uri)
    {
        ArtifactSandbox.Decide(uri).Should().Be(ArtifactNavigation.Allow);
    }

    [Theory]
    [InlineData("https://evil.example/steal")]
    [InlineData("http://malware.test/x")]
    public void External_Web_Targets_Open_Externally(string uri)
    {
        ArtifactSandbox.Decide(uri).Should().Be(ArtifactNavigation.OpenExternal);
    }

    [Theory]
    [InlineData("file:///C:/Windows/system32/x")]
    [InlineData("ftp://host/x")]
    [InlineData("javascript:alert(1)")]
    public void Other_Schemes_Are_Denied(string uri)
    {
        ArtifactSandbox.Decide(uri).Should().Be(ArtifactNavigation.Deny);
    }

    // ── User-gesture gate (Phase 6 hotfix) ─────────────────────────────────

    [Theory]
    [InlineData(ArtifactNavigation.OpenExternal, true, true)]   // user click on an external link
    [InlineData(ArtifactNavigation.OpenExternal, false, false)] // artifact JS window.open — suppressed
    [InlineData(ArtifactNavigation.Allow, true, false)]         // allowed in-frame; never "external"
    [InlineData(ArtifactNavigation.Deny, true, false)]          // denied outright
    public void ShouldOpenExternal_Only_For_User_Initiated_External(
        ArtifactNavigation decision, bool userInitiated, bool expected)
    {
        ArtifactSandbox.ShouldOpenExternal(decision, userInitiated).Should().Be(expected);
    }
}
