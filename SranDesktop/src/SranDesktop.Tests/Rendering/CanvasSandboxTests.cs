using FluentAssertions;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Phase 15 — ADVERSARIAL tests for the Canvas sandbox.
///
/// Canvas runs model-authored JavaScript, so these are written from the
/// attacker's side: each test is something a hostile artifact would try. If
/// any of them starts passing for the artifact, the sandbox has been broken.
///
/// The one guarantee NOT expressible here is the absence of a native bridge —
/// AddHostObjectToScript is simply never called (see CanvasWindow). That is
/// asserted structurally by CanvasHostObjectTests below.
/// </summary>
public sealed class CanvasSandboxSecurityTests
{
    // ── Exfiltration lock ───────────────────────────────────────────────────

    [Fact]
    public void Network_Access_Is_Denied_Outright()
    {
        // connect-src 'none' is what stops fetch / XHR / WebSocket /
        // sendBeacon. Without it, artifact JS could ship the conversation
        // anywhere. Do not relax.
        CanvasSandbox.ContentSecurityPolicy.Should().Contain("connect-src 'none'");
        CanvasSandbox.ContentSecurityPolicy.Should().Contain("default-src 'none'");
    }

    [Fact]
    public void Scripts_Load_Only_From_The_Bundled_Canvas_Host()
    {
        var csp = CanvasSandbox.ContentSecurityPolicy;

        csp.Should().Contain("script-src");
        csp.Should().Contain(CanvasSandbox.LibBase);
        // No CDN may ever appear — the libs are vendored so this works offline
        // and cannot be swapped under us.
        csp.Should().NotContain("unpkg.com");
        csp.Should().NotContain("jsdelivr");
        csp.Should().NotContain("cdn.");
        csp.Should().NotContain("*");
    }

    [Fact]
    public void Form_Submission_And_Base_Rewriting_Are_Blocked()
    {
        // Both are navigation-shaped exfiltration routes that survive
        // connect-src.
        CanvasSandbox.ContentSecurityPolicy.Should().Contain("form-action 'none'");
        CanvasSandbox.ContentSecurityPolicy.Should().Contain("base-uri 'none'");
    }

    // ── Host isolation ──────────────────────────────────────────────────────

    [Fact]
    public void Canvas_Has_Its_Own_Host_Separate_From_Artifacts_Katex_And_Pdf()
    {
        // Sharing a host would let a canvas address another surface's origin,
        // and would make one mapping change silently widen another surface.
        CanvasSandbox.VirtualHost.Should().NotBe(ArtifactSandbox.VirtualHost);
        CanvasSandbox.VirtualHost.Should().NotBe(PdfPreviewSandbox.VirtualHost);
    }

    [Theory]
    [InlineData("https://appassets.sran/react.production.min.js")] // artifact/KaTeX host
    [InlineData("https://pdfpreview.sran/abc.pdf")]                // PDF host
    public void An_Artifact_Cannot_Navigate_To_Another_Surfaces_Host(string uri)
    {
        CanvasSandbox.IsNavigationAllowed(uri).Should().BeFalse();
    }

    // ── Navigation lock ─────────────────────────────────────────────────────

    [Fact]
    public void The_Bundled_Libs_Host_Is_Allowed()
    {
        CanvasSandbox.IsNavigationAllowed($"{CanvasSandbox.LibBase}/react.production.min.js")
            .Should().BeTrue();
    }

    [Fact]
    public void The_Staged_Document_Host_Is_Allowed()
    {
        // The document is served over https from its own host rather than
        // through NavigateToString, whose data: URI this policy denies — the
        // Phase 15 bug where the canvas cancelled its own document.
        CanvasSandbox.IsNavigationAllowed(CanvasSandbox.DocumentUrl("abc.html"))
            .Should().BeTrue();
    }

    [Fact]
    public void The_Document_Host_Is_Distinct_From_The_Libs_Host()
    {
        // Separate origins so the artifact's own document cannot read the
        // library folder as same-origin.
        CanvasSandbox.DocumentHost.Should().NotBe(CanvasSandbox.VirtualHost);
        CanvasSandbox.DocumentHost.Should().NotBe(ArtifactSandbox.VirtualHost);
        CanvasSandbox.DocumentHost.Should().NotBe(PdfPreviewSandbox.VirtualHost);
    }

    [Fact]
    public void Data_Urls_Stay_Denied_Even_Though_We_Load_Documents_Ourselves()
    {
        // The fix was to stop USING data:, not to start allowing it. An
        // artifact navigating itself to a data: document is still refused.
        CanvasSandbox.IsNavigationAllowed("data:text/html;base64,PGgxPngK")
            .Should().BeFalse();
    }

    [Theory]
    [InlineData("https://evil.example.com/steal")]
    [InlineData("http://canvas.sran/x.js")]          // downgraded scheme
    [InlineData("file:///C:/Windows/win.ini")]
    [InlineData("javascript:alert(1)")]
    [InlineData("data:text/html,<script>alert(1)</script>")]
    [InlineData("mailto:someone@example.com")]
    [InlineData("ms-appx-web:///x")]
    public void Every_Other_Navigation_Target_Is_Cancelled(string uri)
    {
        CanvasSandbox.IsNavigationAllowed(uri).Should().BeFalse($"uri was \"{uri}\"");
    }

    [Fact]
    public void There_Is_No_Open_In_Browser_Escape_For_Canvas()
    {
        // The R9 artifact panel deliberately HAS one (ArtifactSandbox returns
        // OpenExternal for https). A canvas is arbitrary model output, so the
        // same target is simply denied here.
        ArtifactSandbox.Decide("https://example.com/")
            .Should().Be(ArtifactNavigation.OpenExternal, "the artifact panel keeps its escape hatch");
        CanvasSandbox.IsNavigationAllowed("https://example.com/")
            .Should().BeFalse("canvas has none");
    }

    // ── The postMessage channel ─────────────────────────────────────────────

    [Fact]
    public void A_Valid_Message_From_The_Current_Render_Is_Accepted()
    {
        var token = CanvasSandbox.NewRenderToken();
        var json = $$"""{"__sranCanvas":true,"token":"{{token}}","type":"error","payload":"boom"}""";

        CanvasSandbox.TryReadMessage(json, token, out var type, out var payload).Should().BeTrue();
        type.Should().Be("error");
        payload.Should().Be("boom");
    }

    [Fact]
    public void A_Message_From_A_Stale_Render_Is_Rejected()
    {
        // The document being torn down must not be able to write into the UI
        // of the one that replaced it.
        var old = CanvasSandbox.NewRenderToken();
        var current = CanvasSandbox.NewRenderToken();
        var json = $$"""{"__sranCanvas":true,"token":"{{old}}","type":"error","payload":"x"}""";

        CanvasSandbox.TryReadMessage(json, current, out _, out _).Should().BeFalse();
    }

    [Theory]
    [InlineData("""{"type":"error","payload":"x"}""")]                       // no marker, no token
    [InlineData("""{"__sranCanvas":true,"type":"error","payload":"x"}""")]    // no token
    [InlineData("""{"__sranCanvas":"true","token":"T","type":"error"}""")]    // marker not boolean true
    [InlineData("""{"__sranCanvas":true,"token":"T","type":"eval"}""")]       // unknown type
    [InlineData("""{"__sranCanvas":true,"token":"T","type":123}""")]          // type not a string
    [InlineData("""["__sranCanvas",true]""")]                                 // not an object
    [InlineData("not json at all")]
    [InlineData("")]
    [InlineData(null)]
    public void A_Forged_Or_Malformed_Message_Is_Rejected(string? json)
    {
        CanvasSandbox.TryReadMessage(json, "T", out _, out _).Should().BeFalse();
    }

    [Fact]
    public void An_Oversized_Error_Payload_Is_Truncated()
    {
        // A hostile artifact must not be able to flood the UI (or the log)
        // through the one channel it has.
        var token = "T";
        var flood = new string('x', 10_000);
        var json = $$"""{"__sranCanvas":true,"token":"{{token}}","type":"error","payload":"{{flood}}"}""";

        CanvasSandbox.TryReadMessage(json, token, out _, out var payload).Should().BeTrue();
        payload.Length.Should().Be(500);
    }

    [Fact]
    public void Render_Tokens_Are_Unique_Per_Render()
    {
        var tokens = Enumerable.Range(0, 50).Select(_ => CanvasSandbox.NewRenderToken()).ToList();

        tokens.Distinct().Should().HaveCount(50);
        tokens.Should().OnlyContain(t => t.Length >= 32);
    }
}

/// <summary>
/// Structural guarantee: no native bridge is ever handed to a canvas.
///
/// This reads the Canvas window's source because the property is an ABSENCE —
/// there is no runtime API that reports "no host object was registered", and
/// the whole security model rests on it staying absent.
/// </summary>
public sealed class CanvasHostObjectTests
{
    private static string CanvasWindowSource()
    {
        // Walk up from the test binary to the repo's source tree.
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "SranDesktop.Presentation")))
            dir = dir.Parent;
        dir.Should().NotBeNull("the Presentation project must be locatable from the test output");
        var path = Path.Combine(dir!.FullName, "SranDesktop.Presentation",
            "Views", "Dialogs", "CanvasWindow.xaml.cs");
        File.Exists(path).Should().BeTrue($"expected the canvas window at {path}");
        return File.ReadAllText(path);
    }

    /// <summary>Source with comment lines removed. The comments explain WHY a
    /// dangerous API is absent and naturally name it, so an assertion about
    /// what the file CALLS has to look at code only.</summary>
    private static string CanvasWindowCode() =>
        string.Join('\n', CanvasWindowSource()
            .Split('\n')
            .Where(line => !line.TrimStart().StartsWith("//", StringComparison.Ordinal)));

    [Fact]
    public void The_Canvas_Window_Never_Registers_A_Host_Object()
    {
        // AddHostObjectToScript would expose a .NET object graph to artifact
        // JS — the single change that would turn "runs code" into "runs code
        // on the user's machine".
        CanvasWindowCode().Should().NotContain("AddHostObjectToScript");
    }

    [Fact]
    public void The_Canvas_Window_Keeps_Dev_Tools_And_New_Windows_Off()
    {
        var source = CanvasWindowCode();

        source.Should().Contain("AreDevToolsEnabled = false");
        source.Should().Contain("NewWindowRequested");
        source.Should().Contain("e.Handled = true");
    }

    [Fact]
    public void The_Canvas_Host_Folder_Mapping_Is_Read_Only_DenyCors()
    {
        CanvasWindowCode().Should().Contain("CoreWebView2HostResourceAccessKind.DenyCors");
    }
}

/// <summary>Phase 15 — the generated documents carry the posture.</summary>
public sealed class CanvasHtmlBuilderTests
{
    private const string Token = "tok123";

    [Theory]
    [InlineData("html", "<p>hi</p>")]
    [InlineData("react", "export default function App(){return <b>hi</b>}")]
    [InlineData("mermaid", "graph TD; A-->B;")]
    [InlineData("svg", "<svg><circle r='5'/></svg>")]
    [InlineData("markdown", "# hi")]
    public void Every_Document_Carries_The_Csp_And_The_Bootstrap(string type, string source)
    {
        var html = CanvasHtmlBuilder.Build(type, source, "t", dark: true, Token);

        html.Should().Contain("Content-Security-Policy");
        html.Should().Contain("connect-src 'none'");
        html.Should().Contain(Token, "the bootstrap stamps the render token");
    }

    [Fact]
    public void A_Full_Html_Document_Still_Gets_Our_Csp_Injected()
    {
        // Multiple CSP metas INTERSECT, so an artifact shipping its own looser
        // policy can never relax ours.
        var attacker =
            "<html><head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src *\">" +
            "</head><body>x</body></html>";

        var html = CanvasHtmlBuilder.Build("html", attacker, "t", dark: false, Token);

        html.Should().Contain(CanvasSandbox.CspMetaTag());
    }

    [Fact]
    public void Library_References_Point_At_The_Canvas_Host_Only()
    {
        var react = CanvasHtmlBuilder.Build("react", "export default function App(){return null}", "t", true, Token);

        react.Should().Contain($"{CanvasSandbox.LibBase}/react.production.min.js");
        react.Should().Contain($"{CanvasSandbox.LibBase}/babel.min.js");
        react.Should().NotContain("unpkg.com");
        react.Should().NotContain("jsdelivr");
        react.Should().NotContain(ArtifactSandbox.LibBase);
    }

    [Fact]
    public void Mermaid_Runs_In_Strict_Security_Mode_With_Escaped_Source()
    {
        var html = CanvasHtmlBuilder.Build("mermaid", "graph TD; A[<b>x</b>]-->B;", "t", false, Token);

        html.Should().Contain("securityLevel:'strict'");
        html.Should().Contain("&lt;b&gt;", "diagram source is embedded as TEXT, never as markup");
        html.Should().NotContain("<b>x</b>");
    }

    [Fact]
    public void Svg_Is_Sanitized_Before_It_Is_Embedded()
    {
        var hostile =
            "<svg onload=\"alert(1)\"><script>alert(2)</script>" +
            "<foreignObject><iframe src=\"https://evil\"></iframe></foreignObject>" +
            "<a xlink:href=\"javascript:alert(3)\">x</a></svg>";

        var html = CanvasHtmlBuilder.Build("svg", hostile, "t", false, Token);

        html.Should().NotContain("onload=");
        html.Should().NotContain("alert(2)");
        html.Should().NotContain("<foreignObject");
        html.Should().NotContain("javascript:");
    }

    [Fact]
    public void Markdown_Is_Previewed_As_Escaped_Text_Not_Converted_Html()
    {
        var html = CanvasHtmlBuilder.Build("markdown", "# hi <img src=x onerror=alert(1)>", "t", false, Token);

        html.Should().Contain("&lt;img");
        html.Should().NotContain("<img src=x");
    }

    [Fact]
    public void React_Source_Cannot_Close_The_Wrapper_Script_Tag()
    {
        var breakout = "const x = 1; </script><script>alert('escaped')</script>";

        var html = CanvasHtmlBuilder.Build("react", breakout, "t", false, Token);

        html.Should().NotContain("</script><script>alert");
        html.Should().Contain("<\\/script");
    }

    [Fact]
    public void React_Module_Syntax_Is_Rewritten_For_The_Classic_Script_World()
    {
        var src = "import React from 'react';\nexport default function App(){return null}\nexport const x = 1;";

        var processed = CanvasHtmlBuilder.PreprocessReactSource(src);

        processed.Should().NotContain("import React");
        processed.Should().Contain("const __sranDefault = function App()");
        processed.Should().Contain("const x = 1;");
    }
}

/// <summary>Phase 15 — which blocks are eligible, and the shared lineage key.</summary>
public sealed class CanvasEligibilityTests
{
    [Theory]
    [InlineData("html", "html")]
    [InlineData("htm", "html")]
    [InlineData("HTML", "html")]
    [InlineData("md", "markdown")]
    [InlineData("markdown", "markdown")]
    [InlineData("svg", "svg")]
    [InlineData("mermaid", "mermaid")]
    [InlineData("jsx", "react")]
    [InlineData("tsx", "react")]
    [InlineData("react", "react")]
    public void Renderable_Languages_Map_To_A_Canvas_Type(string lang, string expected)
    {
        CanvasEligibility.TypeFor(lang, "x").Should().Be(expected);
    }

    [Theory]
    [InlineData("python")]
    [InlineData("js")]
    [InlineData("json")]
    [InlineData("bash")]
    [InlineData("sql")]
    [InlineData("csharp")]
    public void Everything_Else_Keeps_The_Plain_Code_Block(string lang)
    {
        CanvasEligibility.TypeFor(lang, "print(1)").Should().BeNull();
    }

    [Theory]
    [InlineData("", "<svg viewBox='0 0 10 10'></svg>")]
    [InlineData("xml", "<?xml version='1.0'?><svg></svg>")]
    [InlineData("", "  \n <svg/>")]
    public void An_Unlabelled_Svg_Document_Is_Still_Detected(string lang, string content)
    {
        CanvasEligibility.TypeFor(lang, content).Should().Be("svg");
    }

    [Theory]
    [InlineData("", "just some prose")]
    [InlineData("xml", "<config><a/></config>")]
    public void An_Unlabelled_Non_Svg_Block_Is_Not_Eligible(string lang, string content)
    {
        CanvasEligibility.TypeFor(lang, content).Should().BeNull();
    }

    [Fact]
    public void A_Huge_Block_Is_Refused_So_The_Browser_Cannot_Be_Frozen()
    {
        var huge = new string('x', CanvasEligibility.MaxCanvasChars + 1);

        CanvasEligibility.TypeFor("html", huge).Should().Be("html", "the type is still known");
        CanvasEligibility.CanOpen("html", huge).Should().BeFalse("but the button is not offered");
        CanvasEligibility.CanOpen("html", new string('x', CanvasEligibility.MaxCanvasChars))
            .Should().BeTrue();
    }

    [Fact]
    public void The_Lineage_Key_Matches_The_Web_Derivation()
    {
        // djb2 over the source, base36 — computed here against the values the
        // web's artifactKeyFor produces for the same inputs. If this drifts,
        // the desktop and the web write TWO histories for one artifact.
        // "" → seed 5381 → base36 "45h"; "a" → 5381*33+97 = 177670 → "3t3a".
        CanvasEligibility.ArtifactKeyFor("m1", "").Should().Be("m1:45h");
        CanvasEligibility.ArtifactKeyFor("m1", "a").Should().Be("m1:3t3a");
        CanvasEligibility.ArtifactKeyFor("m1", "<p>one</p>").Should().Be("m1:1gywv0q");
        CanvasEligibility.ArtifactKeyFor(null, "a").Should().Be("adhoc:3t3a");
    }

    [Fact]
    public void The_Key_Is_Stable_For_The_Same_Block_And_Distinct_Across_Blocks()
    {
        var a = CanvasEligibility.ArtifactKeyFor("m1", "<p>one</p>");
        var again = CanvasEligibility.ArtifactKeyFor("m1", "<p>one</p>");
        var other = CanvasEligibility.ArtifactKeyFor("m1", "<p>two</p>");
        var otherMessage = CanvasEligibility.ArtifactKeyFor("m2", "<p>one</p>");

        again.Should().Be(a);
        other.Should().NotBe(a, "two blocks in one message stay separate lineages");
        otherMessage.Should().NotBe(a);
    }
}
