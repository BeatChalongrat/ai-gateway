using FluentAssertions;
using Markdig.Extensions.Mathematics;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Phase 12 C: math node detection through the renderer's EXACT pipeline
/// (MarkdownRenderer.ParseDocument) — the same guarantee BlockDiff relies on.
/// The WebView2/KaTeX visual output itself is not unit-testable; these tests
/// cover detection, the streaming guard, and the pure builders.
/// </summary>
public class MathPipelineTests
{
    private static MarkdownDocument Parse(string markdown) =>
        MarkdownRenderer.ParseDocument(markdown);

    [Fact]
    public void Inline_Dollar_Math_Produces_MathInline()
    {
        var doc = Parse("the value $x^2 + 1$ grows");

        var math = doc.Descendants().OfType<MathInline>().Single();
        math.Content.ToString().Should().Be("x^2 + 1");
        math.DelimiterCount.Should().Be(1);
    }

    // ── Currency vs inline math (audit A4) ────────────────────────────────
    // Markdig refuses a closing '$' that is immediately followed by a DIGIT,
    // which is what keeps "$100 ถึง $200" out of math while "$x$", "$100$"
    // and "$2x + 5$" stay math. The web client had no such rule and
    // typeset the text between two amounts; lib/remarkCurrencyGuard.ts
    // ports THIS behaviour, so these cases are the cross-client contract —
    // Markdown.test.tsx asserts the same six inputs.

    [Theory]
    [InlineData("ราคาอยู่ระหว่าง $100 ถึง $200")]
    [InlineData("between $100 and $200")]
    [InlineData("costs $100-$200 total")]
    [InlineData("$100 today")]
    public void Currency_Pairs_Are_Not_Math(string src)
    {
        Parse(src).Descendants().OfType<MathInline>().Should().BeEmpty(
            "a closing '$' followed by a digit starts the next amount, not a formula");
    }

    [Theory]
    [InlineData("the value $x$ grows", "x")]
    [InlineData("solve $2x + 5$ now", "2x + 5")]
    [InlineData("exactly $100$ baht", "100")]
    public void Genuine_Single_Dollar_Math_Still_Renders(string src, string expected)
    {
        var math = Parse(src).Descendants().OfType<MathInline>().Single();

        math.Content.ToString().Should().Be(expected);
    }

    [Fact]
    public void Double_Dollar_Block_Produces_Closed_MathBlock()
    {
        var doc = Parse("before\n\n$$\nE = mc^2\n$$\n\nafter");

        var block = doc.OfType<MathBlock>().Single();
        block.Lines.ToString().Should().Be("E = mc^2");
        MathGuard.IsClosed(block).Should().BeTrue();
    }

    [Fact]
    public void Single_Line_Double_Dollar_Paragraph_Upgrades_To_Display_Math()
    {
        // The common LLM form: $$...$$ on one line parses as a paragraph
        // holding a single MathInline with a double delimiter.
        var doc = Parse("$$E = mc^2$$");

        var paragraph = doc.OfType<ParagraphBlock>().Single();
        var display = MathGuard.AsDisplayMath(paragraph);
        display.Should().NotBeNull();
        display!.Content.ToString().Should().Be("E = mc^2");
    }

    [Fact]
    public void Mixed_Paragraph_Does_Not_Upgrade_To_Display_Math()
    {
        var doc = Parse("$$x$$ plus trailing words");

        var paragraph = doc.OfType<ParagraphBlock>().Single();
        MathGuard.AsDisplayMath(paragraph).Should().BeNull();
    }

    [Fact]
    public void Single_Dollar_Paragraph_Does_Not_Upgrade_To_Display_Math()
    {
        var doc = Parse("$x^2$");

        var paragraph = doc.OfType<ParagraphBlock>().Single();
        MathGuard.AsDisplayMath(paragraph).Should().BeNull();
    }

    [Fact]
    public void Currency_Dollars_Do_Not_Produce_Math_Nodes()
    {
        var doc = Parse("it costs $5 and $10 total");

        doc.Descendants().OfType<MathInline>().Should().BeEmpty();
        doc.OfType<MathBlock>().Should().BeEmpty();
    }

    [Fact]
    public void Unclosed_Inline_Dollar_Degrades_To_Literal_Text()
    {
        var doc = Parse("open $x plus");

        doc.Descendants().OfType<MathInline>().Should().BeEmpty();
        doc.Descendants().OfType<LiteralInline>().Single()
            .Content.ToString().Should().Be("open $x plus");
    }

    [Fact]
    public void Double_Dollar_Inside_Code_Fence_Stays_Code()
    {
        // The parser-based guard can never false-positive on a $$ that lives
        // inside a code fence — Markdig already classified it as code.
        var doc = Parse("```\n$$\nx\n$$\n```");

        doc.OfType<MathBlock>().Should().BeEmpty();
        doc.OfType<FencedCodeBlock>().Should().ContainSingle();
    }

    [Fact]
    public void MathBlock_Derives_From_FencedCodeBlock_So_Switch_Order_Matters()
    {
        // Regression guard for RenderBlock's case order: if MathBlock were
        // matched by the FencedCodeBlock arm, $$…$$ would render as a code
        // block (the pre-Phase-12 behavior).
        typeof(FencedCodeBlock).IsAssignableFrom(typeof(MathBlock)).Should().BeTrue();
    }
}

/// <summary>The math streaming guard: an unterminated $$ mid-stream defers —
/// no math surface until the block completes (Phase 4 fence-guard parity).</summary>
public class MathStreamingGuardTests
{
    [Fact]
    public void Chunk_Ending_Inside_Block_Math_Is_Unclosed_And_Defers()
    {
        var doc = MarkdownRenderer.ParseDocument("text\n\n$$\nE = mc");

        var block = doc.OfType<MathBlock>().Single();
        MathGuard.IsClosed(block).Should().BeFalse();
        MathGuard.ShouldRenderKatex(block, streaming: true).Should().BeFalse();
        // Even the final render never KaTeXes an unclosed block.
        MathGuard.ShouldRenderKatex(block, streaming: false).Should().BeFalse();
    }

    [Fact]
    public void Closed_Block_Defers_While_Streaming_And_Renders_When_Done()
    {
        // No WebView2 churn on the 150 ms streaming ticks: KaTeX waits for
        // the authoritative post-stream render.
        var doc = MarkdownRenderer.ParseDocument("$$\nE = mc^2\n$$");

        var block = doc.OfType<MathBlock>().Single();
        MathGuard.ShouldRenderKatex(block, streaming: true).Should().BeFalse();
        MathGuard.ShouldRenderKatex(block, streaming: false).Should().BeTrue();
    }

    [Fact]
    public void Trailing_Lone_Dollar_Stays_Literal_Text()
    {
        var doc = MarkdownRenderer.ParseDocument("price is $");

        doc.Descendants().OfType<MathInline>().Should().BeEmpty();
        doc.Descendants().OfType<LiteralInline>().Single()
            .Content.ToString().Should().Be("price is $");
    }

    [Fact]
    public void Half_Typed_Inline_Math_Chunk_Produces_No_Math_Node()
    {
        // "$x^2 + y" (closing $ not yet streamed) must not flash as math.
        var doc = MarkdownRenderer.ParseDocument("solve $x^2 + y");

        doc.Descendants().OfType<MathInline>().Should().BeEmpty();
    }
}

public class MathTexTests
{
    [Fact]
    public void Greek_And_Operators_Map_To_Unicode()
    {
        MathTex.ToInlineText(@"\alpha \times \beta \leq \infty").Should().Be("α × β ≤ ∞");
    }

    [Fact]
    public void Superscript_Digits_Map()
    {
        MathTex.ToInlineText("x^2 + y^{10}").Should().Be("x² + y¹⁰");
    }

    [Fact]
    public void Subscripts_Map()
    {
        MathTex.ToInlineText("a_1 + a_{ij}").Should().Be("a₁ + aᵢⱼ");
    }

    [Fact]
    public void Simple_Fraction_Flattens()
    {
        MathTex.ToInlineText(@"\frac{\pi}{2}").Should().Be("π/2");
    }

    [Fact]
    public void Compound_Fraction_Keeps_Parens()
    {
        MathTex.ToInlineText(@"\frac{a+b}{2}").Should().Be("(a+b)/2");
    }

    [Fact]
    public void Sqrt_Maps()
    {
        MathTex.ToInlineText(@"\sqrt{2}").Should().Be("√2");
        MathTex.ToInlineText(@"\sqrt{x+1}").Should().Be("√(x+1)");
    }

    [Fact]
    public void Unknown_Command_Is_Preserved_Literally()
    {
        // Malformed/unsupported math degrades to text — nothing is lost.
        MathTex.ToInlineText(@"\undefinedcmd{x}").Should().Contain(@"\undefinedcmd");
    }

    [Fact]
    public void Text_Wrappers_Unwrap()
    {
        MathTex.ToInlineText(@"\text{speed} = v").Should().Be("speed = v");
    }

    [Fact]
    public void Mathbb_Single_Letter_Maps_To_Double_Struck()
    {
        MathTex.ToInlineText(@"x \in \mathbb{R}").Should().Be("x ∈ ℝ");
    }

    [Fact]
    public void Left_Right_Are_Stripped()
    {
        MathTex.ToInlineText(@"\left( x \right)").Should().Be("( x )");
    }

    [Fact]
    public void Degrees_Map()
    {
        MathTex.ToInlineText(@"45^\circ").Should().Be("45°");
    }

    [Fact]
    public void Empty_And_Whitespace_Return_Empty()
    {
        MathTex.ToInlineText("").Should().Be("");
        MathTex.ToInlineText("   ").Should().Be("");
    }
}

public class MathHtmlBuilderTests
{
    [Fact]
    public void Carries_The_Artifact_Sandbox_Csp()
    {
        MathHtmlBuilder.Build("x", dark: true).Should().Contain(ArtifactSandbox.CspMetaTag());
    }

    [Fact]
    public void References_Only_The_Bundled_Katex_Assets()
    {
        var html = MathHtmlBuilder.Build("x", dark: true);

        html.Should().Contain($"{ArtifactSandbox.LibBase}/katex/katex.min.js");
        html.Should().Contain($"{ArtifactSandbox.LibBase}/katex/katex.min.css");
        html.Should().NotContain("unpkg.com").And.NotContain("jsdelivr");
    }

    [Fact]
    public void Tex_Is_Json_Escaped_Into_The_Script()
    {
        var html = MathHtmlBuilder.Build("\\frac{a}{b} \"quoted\"\nline2", dark: false);

        html.Should().Contain("\"\\\\frac{a}{b} \\u0022quoted\\u0022\\nline2\"");
    }

    [Fact]
    public void Renders_Display_Mode_And_Never_Throws_On_Malformed_Math()
    {
        var html = MathHtmlBuilder.Build("x", dark: true);

        html.Should().Contain("displayMode: true");
        html.Should().Contain("throwOnError: false");
    }

    [Fact]
    public void Theme_Colors_Follow_Dark_And_Light()
    {
        MathHtmlBuilder.Build("x", dark: true).Should().Contain("#ECECEC");
        MathHtmlBuilder.Build("x", dark: false).Should().Contain("#0D0D0D");
    }

    [Fact]
    public void Vendored_Bundle_Exists_And_Matches_The_Pinned_Version()
    {
        // Assets/WebLibs flows to the test output via the Presentation
        // project's Content items — same mechanism the app relies on.
        var katexDir = Path.Combine(AppContext.BaseDirectory, "Assets", "WebLibs", "katex");
        var js = Path.Combine(katexDir, "katex.min.js");

        File.Exists(js).Should().BeTrue("KaTeX must be vendored, not fetched");
        File.Exists(Path.Combine(katexDir, "katex.min.css")).Should().BeTrue();
        File.Exists(Path.Combine(katexDir, "fonts", "KaTeX_Main-Regular.woff2")).Should().BeTrue();
        File.ReadAllText(js).Should().Contain($"version:\"{MathHtmlBuilder.KatexVersion}\"");
    }
}
