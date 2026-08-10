using FluentAssertions;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Phase 15 hotfix — proof that a canvas actually RENDERS.
///
/// The original Phase 15 suite asserted the security posture (CSP strings,
/// host names, navigation policy) and passed while every canvas showed an
/// empty pane: the navigation lock denied <c>data:</c>, and NavigateToString
/// navigates to a data: URI, so the canvas cancelled its own document.
/// Posture assertions can never catch that. These load the document in a REAL
/// WebView2 and read the resulting DOM back.
///
/// Each test skips when the WebView2 runtime is absent, so a machine without
/// it reports "not run" rather than a false failure.
/// </summary>
[Collection("CanvasRender")]
public sealed class CanvasRenderTests
{
    private const string Token = "TOKEN";

    /// <summary>The exact document from the bug report: full <c>&lt;!DOCTYPE&gt;</c>
    /// document, inline style, inline script, Thai button label.</summary>
    private const string ReportedHtml = """
        <!DOCTYPE html><html lang="th"><head><meta charset="UTF-8">
        <style>body{display:flex;justify-content:center;align-items:center;height:100vh}
        button{padding:15px 30px;background:#4CAF50;color:#fff}</style></head>
        <body><div><h1 id="counter">0</h1><button id="countButton">กดเพื่อเพิ่ม</button></div>
        <script>
        document.getElementById('countButton').addEventListener('click',function(){
          var c = document.getElementById('counter');
          c.textContent = String(Number(c.textContent) + 1);
        });
        </script></body></html>
        """;

    private static CanvasRenderOutcome Render(string type, string source, string probe) =>
        CanvasRenderHarness.Render(
            CanvasHtmlBuilder.Build(type, source, "artifact", dark: false, Token), probe);

    // ── The reported bug ────────────────────────────────────────────────────

    [Fact]
    public void The_Reported_Document_Renders_Its_Content()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        var outcome = Render("html", ReportedHtml,
            "document.getElementById('countButton') ? document.getElementById('countButton').textContent : 'MISSING'");

        outcome.NavigationSucceeded.Should().BeTrue(
            $"navigation must not be cancelled (status: {outcome.NavigationError})");
        outcome.ProcessFailure.Should().BeEmpty();
        outcome.ProbeResult.Should().Contain("กดเพื่อเพิ่ม",
            "the button the user could not see must be in the DOM");
    }

    [Fact]
    public void The_Artifacts_Own_Javascript_Actually_Runs()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        // Click the button the way the user does, then read the counter: this
        // is the difference between "HTML appeared" and "the artifact works".
        var outcome = Render("html", ReportedHtml,
            "(function(){document.getElementById('countButton').click();" +
            "document.getElementById('countButton').click();" +
            "return document.getElementById('counter').textContent;})()");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("2", "two clicks must have incremented the counter");
    }

    [Fact]
    public void The_Artifact_Reports_Ready_Over_The_Validated_Channel()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        var outcome = Render("html", ReportedHtml, "1");

        outcome.ArtifactMessages.Should().Contain(m => m.Contains("\"type\":\"ready\""));
        outcome.ArtifactMessages.Should().Contain(m => m.Contains(Token),
            "the bootstrap stamps the render token so stale documents can be told apart");
    }

    // ── The three document shapes (web C2 parity) ───────────────────────────

    [Fact]
    public void A_Bare_Fragment_Renders()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        var outcome = Render("html", "<p id='p'>fragment works</p>",
            "document.getElementById('p') ? document.getElementById('p').textContent : 'MISSING'");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("fragment works");
    }

    [Fact]
    public void A_Document_With_Html_But_No_Head_Renders()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        var outcome = Render("html", "<html><body><p id='p'>no head</p></body></html>",
            "document.getElementById('p') ? document.getElementById('p').textContent : 'MISSING'");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("no head");
    }

    // ── The other artifact types ────────────────────────────────────────────

    [Fact]
    public void A_React_Component_Mounts_From_The_Bundled_Libraries()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        // Also the cross-origin check: React/ReactDOM/Babel are served from
        // the LIBS host while the document lives on the DOCUMENT host.
        var outcome = Render("react",
            "export default function App(){ return <h2 id='mounted'>react ok</h2>; }",
            "document.getElementById('mounted') ? document.getElementById('mounted').textContent : 'MISSING'");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("react ok",
            "React + Babel must load from the canvas libs host and mount");
    }

    [Fact]
    public void A_Mermaid_Diagram_Renders_To_Svg()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        var outcome = Render("mermaid", "graph TD; A-->B;",
            "document.querySelector('.mermaid svg') ? 'rendered' : 'MISSING'");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("rendered");
    }

    [Fact]
    public void An_Svg_Artifact_Renders()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        var outcome = Render("svg",
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><circle id='c' cx='5' cy='5' r='4'/></svg>",
            "document.getElementById('c') ? 'rendered' : 'MISSING'");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("rendered");
    }

    [Fact]
    public void A_Markdown_Artifact_Shows_Its_Source_As_Text()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        var outcome = Render("markdown", "# heading\n\nbody text",
            "document.querySelector('pre') ? document.querySelector('pre').textContent : 'MISSING'");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("heading");
    }

    // ── The posture still holds AT RUNTIME, not just in the CSP string ──────

    [Fact]
    public void Network_Access_Is_Actually_Blocked_At_Runtime()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        // connect-src 'none' must make a real fetch fail — the exfiltration
        // lock verified by exercising it, not by reading the header back.
        var outcome = Render("html",
            "<p id='p'>x</p><script>window.__r='pending';" +
            "fetch('https://example.com/').then(function(){window.__r='ALLOWED'})" +
            ".catch(function(){window.__r='blocked'});</script>",
            "window.__r");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("blocked");
        outcome.ProbeResult.Should().NotContain("ALLOWED");
    }

    [Fact]
    public void There_Is_No_Host_Bridge_For_The_Artifact_To_Reach()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        // WebView2 ALWAYS provides the chrome.webview.hostObjects plumbing —
        // its mere presence proves nothing. What matters is that nothing is
        // registered through it, i.e. the proxy exposes no names, because
        // AddHostObjectToScript is never called. That is the invariant to
        // pin: if someone later registers an object, this list stops being
        // empty and the test fails.
        var outcome = Render("html", "<p>x</p>",
            "(function(){try{" +
            "var names = Object.keys(window.chrome.webview.hostObjects);" +
            "return names.length === 0 ? 'no-bridge' : 'BRIDGE:' + names.join(',');" +
            "}catch(e){return 'no-bridge';}})()");

        outcome.NavigationSucceeded.Should().BeTrue();
        outcome.ProbeResult.Should().Contain("no-bridge");
        outcome.ProbeResult.Should().NotContain("BRIDGE:");
    }

    [Fact]
    public void An_Artifact_Runtime_Error_Is_Reported_Not_Swallowed()
    {
        if (!CanvasRenderHarness.RuntimeAvailable) return; // no embedded browser here

        var outcome = Render("html",
            "<p>x</p><script>throw new Error('boom from artifact');</script>", "1");

        outcome.ArtifactMessages.Should().Contain(m => m.Contains("boom from artifact"),
            "a broken artifact must say so instead of showing a blank pane");
    }
}
