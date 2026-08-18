using FluentAssertions;
using Markdig.Extensions.Mathematics;
using Markdig.Syntax;
using SranDesktop.Presentation.Controls;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Batch 2 T1 — the SAME corpus as the web side
/// (apps/web-chat/src/lib/mathDelimiters.test.ts). The two normalizers
/// must agree byte-for-byte; when one corpus changes, change both.
/// </summary>
public class MathDelimiterTests
{
    private static string N(string s) => MathDelimiters.Normalize(s);

    // ── conversions ─────────────────────────────────────────────────

    [Fact]
    public void Standalone_Display_Becomes_A_DollarDollar_Block() =>
        N("\\[x^2\\]").Should().Be("$$\nx^2\n$$");

    [Fact]
    public void MidParagraph_Display_Becomes_Inline_DollarDollar() =>
        N("the result \\[x^2\\] follows").Should().Be("the result $$x^2$$ follows");

    [Fact]
    public void Inline_Parens_Become_Single_Dollar() =>
        N("\\(a+b\\)").Should().Be("$a+b$");

    [Fact]
    public void MultiLine_Display_Block_Converts() =>
        N("\\[\nE = mc^2\n\\]").Should().Be("$$\nE = mc^2\n$$");

    [Fact]
    public void Thai_Text_Either_Side_No_Whitespace_Assumptions()
    {
        N("ราคา \\(x\\) บาท").Should().Be("ราคา $x$ บาท");
        N("ผลลัพธ์คือ\\[x^2\\]ครับ").Should().Be("ผลลัพธ์คือ$$x^2$$ครับ");
    }

    [Fact]
    public void Several_Delimiters_In_One_Message() =>
        N("first \\(a\\) then \\[b\\] end").Should().Be("first $a$ then $$b$$ end");

    // ── must NOT touch ──────────────────────────────────────────────

    [Fact]
    public void Dollar_Forms_Stay_Unchanged()
    {
        const string src = "display $$E = mc^2$$ and inline $x^2$ stay";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Currency_Stays_Unchanged()
    {
        const string src = "between $100 and $200";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Fenced_Code_Is_Protected()
    {
        const string src = "```latex\n\\[x^2\\]\n```";
        N(src).Should().Be(src);
    }

    [Fact]
    public void CSharp_Attribute_Fence_Is_Protected()
    {
        const string src = "```csharp\n[property: JsonPropertyName(\"x\")] string X\n```";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Inline_Code_Is_Protected()
    {
        const string src = "use `\\[x\\]` to write display math";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Double_Backtick_Inline_Code_Is_Protected()
    {
        const string src = "escape it as ``\\[x\\]`` here";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Unterminated_Display_Stays_Literal_While_Streaming()
    {
        const string src = "the result is \\[x^2 + ";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Chunk_Boundary_Splitting_Backslash_From_Bracket_Stays_Literal()
    {
        const string src = "the result is \\";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Inline_Must_Close_On_The_Same_Line()
    {
        const string src = "value \\(a + b\nmore text \\)";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Escaped_Bracket_Link_Lookalike_Stays_Literal()
    {
        const string src = "\\[not math\\](https://example.com)";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Escaped_Backslashes_Never_Start_A_Delimiter()
    {
        const string src = "literal \\\\[x\\\\] brackets";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Bare_Brackets_Without_Backslash_Stay_Literal()
    {
        const string src = "[property: JsonPropertyName] and [a link](https://x)";
        N(src).Should().Be(src);
    }

    [Fact]
    public void Unclosed_Streaming_Fence_Protects_Its_Content()
    {
        const string src = "```python\nprint('\\[not math\\]')";
        N(src).Should().Be(src);
    }

    [Fact]
    public void DollarDollar_Block_Body_Is_Protected()
    {
        const string src = "$$\n\\[nested\\]\n$$";
        N(src).Should().Be(src);
    }

    // ── streaming stability ─────────────────────────────────────────

    [Fact]
    public void Converted_Prefix_Is_ByteStable_As_The_Tail_Grows()
    {
        const string full = "intro \\[x^2\\] middle \\(a\\) tail text that keeps growing";
        var closedAt = full.IndexOf("\\)", StringComparison.Ordinal) + 2;
        var converted = N(full[..closedAt]);
        for (var len = closedAt; len <= full.Length; len++)
            N(full[..len]).Should().StartWith(converted);
    }

    [Fact]
    public void Converts_The_Moment_The_Closer_Arrives()
    {
        N("value \\[x^2").Should().Be("value \\[x^2");
        N("value \\[x^2\\]").Should().Be("value $$x^2$$");
    }

    // ── pipeline integration (the renderer's exact parse path) ─────

    [Fact]
    public void Standalone_Backslash_Display_Parses_To_A_Closed_MathBlock()
    {
        var doc = MarkdownRenderer.ParseDocument("\\[\nE = mc^2\n\\]");

        var block = doc.OfType<MathBlock>().Single();
        block.Lines.ToString().Should().Be("E = mc^2");
        MathGuard.IsClosed(block).Should().BeTrue();
    }

    [Fact]
    public void Backslash_Inline_Routes_To_The_Same_MathInline_Path_As_Dollar()
    {
        var doc = MarkdownRenderer.ParseDocument("the value \\(x^2 + 1\\) grows");

        var math = doc.Descendants().OfType<MathInline>().Single();
        math.Content.ToString().Should().Be("x^2 + 1");
        math.DelimiterCount.Should().Be(1);
    }

    [Fact]
    public void Fence_Content_Reaches_The_Parser_Untouched()
    {
        var doc = MarkdownRenderer.ParseDocument("```latex\n\\[x^2\\]\n```");

        doc.Descendants().OfType<MathInline>().Should().BeEmpty();
        doc.OfType<MathBlock>().Should().BeEmpty();
        var fence = doc.OfType<FencedCodeBlock>().Single();
        fence.Lines.ToString().Should().Contain("\\[x^2\\]");
    }
}
