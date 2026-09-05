using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Infrastructure.Mcp.Tools;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// FIX6 — the sidecar's origin resolver must agree, value for value, with the
/// host's TypeScript <c>siteScope()</c> (apps/desktop/test/browser-sites.test.ts
/// §1.1). These are the SAME vectors, in C#: if the two ever diverge, the sidecar
/// would enforce a different origin than the host prompted for, and a grant could
/// silently fail to cover the acted-on site. Keep the two lists identical.
/// </summary>
public class BrowserSiteTests
{
    [Theory]
    [InlineData("https://Example.com/path?q=1", "https://example.com")]
    [InlineData("https://example.com:443/", "https://example.com")]
    [InlineData("http://example.com:80/", "http://example.com")]
    [InlineData("https://example.com:8443/x", "https://example.com:8443")]
    [InlineData("https://docs.example.com", "https://docs.example.com")]
    [InlineData("https://intranet.example.com", "https://intranet.example.com")]
    [InlineData("https://www.example.com", "https://www.example.com")]
    [InlineData("chrome://newtab", "chrome:")]
    [InlineData("about:blank", "about:")]
    public void SiteScope_matches_the_host_origin_rule(string url, string expected) =>
        BrowserSite.SiteScope(url).Should().Be(expected);

    [Theory]
    [InlineData("not a url")]
    [InlineData("")]
    [InlineData(null)]
    public void SiteScope_is_null_for_garbage(string? url) =>
        BrowserSite.SiteScope(url).Should().BeNull();

    [Fact]
    public void Subdomains_and_scheme_are_distinct_origins()
    {
        BrowserSite.SiteScope("https://docs.example.com").Should().NotBe(BrowserSite.SiteScope("https://intranet.example.com"));
        BrowserSite.SiteScope("http://example.com").Should().NotBe(BrowserSite.SiteScope("https://example.com"));
        BrowserSite.SiteScope("https://example.com").Should().NotBe(BrowserSite.SiteScope("https://www.example.com"));
    }

    [Fact]
    public void ResolveTabUrl_picks_the_active_tab_when_no_id_and_the_named_tab_otherwise()
    {
        var tabs = JsonDocument.Parse("""
            { "tabs": [
                { "id": 11, "url": "https://a.example.com/1", "active": false },
                { "id": 22, "url": "https://b.example.com/2", "active": true }
            ] }
            """).RootElement;
        BrowserSite.ResolveTabUrl(tabs, null).Should().Be("https://b.example.com/2");   // active
        BrowserSite.ResolveTabUrl(tabs, 11).Should().Be("https://a.example.com/1");     // by id
        BrowserSite.ResolveTabUrl(tabs, 99).Should().BeNull();                          // absent
    }

    // FIX6-addendum: the AUTHORITATIVE per-site gate must be fail-CLOSED. This test
    // fails if the gate ever returns to fail-OPEN (missing authorizedSite → pass).
    [Fact]
    public async Task Tab_acting_tools_are_the_gate_family_by_DATA_and_REFUSE_a_missing_authorized_site()
    {
        var bridge = new ThrowingBridge();
        IMcpTool click = new BrowserClickTool(bridge);
        IMcpTool read = new BrowserReadPageTool(bridge);
        IMcpTool type = new BrowserTypeTool(bridge);
        IMcpTool find = new BrowserFindTool(bridge);
        IMcpTool shot = new BrowserScreenshotTool(bridge);
        IMcpTool tabs = new BrowserTabsTool(bridge);
        IMcpTool navigate = new BrowserNavigateTool(bridge);
        IMcpTool listTabs = new BrowserListTabsTool(bridge);

        // Family membership is DATA (a marker interface), not a name match:
        foreach (var t in new[] { click, read, type, find, shot, tabs })
            t.Should().BeAssignableTo<IBrowserTabAction>();
        navigate.Should().NotBeAssignableTo<IBrowserTabAction>();  // gates on its destination, not the tab
        listTabs.Should().NotBeAssignableTo<IBrowserTabAction>();  // the bootstrap enumerator

        // The pure gate is fail-CLOSED for the family, and a no-op outside it:
        BrowserSiteGate.RequiresRefusal(click, null).Should().BeTrue();
        BrowserSiteGate.RequiresRefusal(click, "").Should().BeTrue();
        BrowserSiteGate.RequiresRefusal(click, "https://x.example.com").Should().BeFalse();
        BrowserSiteGate.RequiresRefusal(navigate, null).Should().BeFalse();
        BrowserSiteGate.RequiresRefusal(listTabs, null).Should().BeFalse();

        // End-to-end: with NO authorized site, a tab-acting tool REFUSES without ever
        // touching the bridge (ThrowingBridge proves the gate ran BEFORE any act).
        // Were the helper fail-OPEN again, it would fall through to the bridge and throw.
        new ShellSession().CurrentAuthorizedSite = null; // ambient AsyncLocal, this async flow
        var refused = await click.ExecuteAsync(JsonDocument.Parse("""{ "ref": 1 }""").RootElement, CancellationToken.None);
        refused.Success.Should().BeFalse();
        refused.Error.Should().Contain("authorized-site-missing");
    }

    /// <summary>A bridge that MUST NOT be reached: if a browser tool calls it, the
    /// fail-closed gate did not run first, and the test fails loudly.</summary>
    private sealed class ThrowingBridge : IBrowserBridgeService
    {
        public bool IsConnected => true;
        public DateTimeOffset? LastSeenAt => null;
        public event EventHandler? StatusChanged { add { } remove { } }
        public void Start() { }
        public string GetOrCreatePairingToken() => "unused";
        public string RotatePairingToken() => "unused";
        public Task<JsonElement> RequestAsync(string method, object? @params = null, CancellationToken ct = default, TimeSpan? timeout = null) =>
            throw new InvalidOperationException($"the bridge must NOT be called (method={method}) — the per-site gate must refuse first");
    }
}
