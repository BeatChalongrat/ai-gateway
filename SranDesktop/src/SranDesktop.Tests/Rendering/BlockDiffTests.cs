using FluentAssertions;
using Markdig;
using Markdig.Syntax;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

public class BlockDiffTests
{
    // Same extensions as MarkdownRenderer's pipeline.
    private static readonly MarkdownPipeline Pipeline =
        new MarkdownPipelineBuilder().UseAdvancedExtensions().Build();

    private static List<BlockFingerprint> Fingerprint(string markdown) =>
        BlockDiff.Fingerprint(markdown, Markdown.Parse(markdown, Pipeline));

    [Fact]
    public void Appending_To_The_Last_Block_Only_Changes_That_Block()
    {
        var before = Fingerprint("para one\n\npara two");
        var after = Fingerprint("para one\n\npara two continues");

        BlockDiff.FirstChangedIndex(before, after).Should().Be(1);
    }

    [Fact]
    public void New_Block_Appends_Without_Touching_The_Prefix()
    {
        var before = Fingerprint("para one\n\npara two\n\n");
        var after = Fingerprint("para one\n\npara two\n\nthird block");

        // Both earlier paragraphs are byte-identical → only index 2 renders.
        BlockDiff.FirstChangedIndex(before, after).Should().Be(2);
    }

    [Fact]
    public void Unchanged_Document_Still_Rerenders_Only_The_Last_Block()
    {
        // The last block is always treated as changed — Markdig's span for an
        // unclosed fence covers only the opening marker, so the diff can't
        // trust it. Earlier blocks stay reusable.
        var fp = Fingerprint("# heading\n\nbody");

        BlockDiff.FirstChangedIndex(fp, Fingerprint("# heading\n\nbody"))
            .Should().Be(fp.Count - 1);
    }

    [Fact]
    public void Mid_Document_Change_Rerenders_From_That_Block()
    {
        var before = Fingerprint("aaa\n\nbbb\n\nccc");
        var after = Fingerprint("aaa\n\nBBB\n\nccc");

        // ccc's SPAN also shifts if lengths differ — here they don't, but the
        // changed middle block must be index 1 regardless.
        BlockDiff.FirstChangedIndex(before, after).Should().Be(1);
    }

    [Fact]
    public void Unterminated_Code_Fence_Is_One_Growing_Block()
    {
        var v1 = "intro\n\n```python\nprint(";
        var v2 = "intro\n\n```python\nprint(\"hello\")\nfor x in y:";

        var before = Fingerprint(v1);
        var after = Fingerprint(v2);

        before.Should().HaveCount(2); // paragraph + open fence to end-of-text
        after.Should().HaveCount(2);
        BlockDiff.FirstChangedIndex(before, after).Should().Be(1); // intro reusable
    }

    [Fact]
    public void Fewer_Blocks_Than_Before_Rerenders_The_Remaining_Last_Block()
    {
        var before = Fingerprint("aaa\n\nbbb\n\nccc");
        var after = Fingerprint("aaa");

        // The sole remaining block is the last block → index 0; the viewer's
        // trim loop drops the stale elements from there on.
        BlockDiff.FirstChangedIndex(before, after).Should().Be(0);
    }
}
