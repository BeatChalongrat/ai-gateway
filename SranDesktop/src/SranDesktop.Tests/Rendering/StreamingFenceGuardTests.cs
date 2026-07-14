using FluentAssertions;
using SranDesktop.Presentation.Rendering;
using Xunit;

namespace SranDesktop.Tests.Rendering;

public class StreamingFenceGuardTests
{
    [Fact]
    public void No_Fence_Returns_Input_Unchanged()
    {
        var text = "plain **markdown** with a ```code``` block";

        var (visible, kind) = StreamingFenceGuard.TruncateAtOpenFence(text);

        visible.Should().Be(text);
        kind.Should().Be(StreamingFenceKind.None);
    }

    [Fact]
    public void Empty_Input_Is_Unchanged()
    {
        StreamingFenceGuard.TruncateAtOpenFence("").Should()
            .Be(("", StreamingFenceKind.None));
    }

    [Fact]
    public void Open_Unterminated_Plan_Fence_Truncates_With_Doc_Flag()
    {
        var text = "Here is your report.\n\n:::doc-plan\n{\"title\": \"Q3";

        var (visible, kind) = StreamingFenceGuard.TruncateAtOpenFence(text);

        visible.Should().Be("Here is your report.\n\n");
        kind.Should().Be(StreamingFenceKind.Plan);
    }

    [Theory]
    [InlineData(":::excel-plan")]
    [InlineData(":::doc-plan")]
    [InlineData(":::pptx-plan")]
    public void All_Plan_Openings_Flag_Doc(string opening)
    {
        var (visible, kind) = StreamingFenceGuard.TruncateAtOpenFence($"intro\n\n{opening}\n{{");

        visible.Should().Be("intro\n\n");
        kind.Should().Be(StreamingFenceKind.Plan);
    }

    [Fact]
    public void Artifact_Fence_Flags_Artifact()
    {
        var text = "Sure!\n\n:::artifact[title=\"Demo\" type=\"html\"]\n<div>";

        var (visible, kind) = StreamingFenceGuard.TruncateAtOpenFence(text);

        visible.Should().Be("Sure!\n\n");
        kind.Should().Be(StreamingFenceKind.Artifact);
    }

    [Fact]
    public void Completed_Fence_Mid_Text_Still_Truncates_At_Opening()
    {
        // Documented behavior: fence bodies are never useful raw, and text
        // AFTER a completed fence stays hidden while streaming — the final
        // render (fences stripped, cards built) restores everything.
        var text = "before\n\n:::excel-plan\n{\"sheets\":[]}\n:::\n\nafter the fence";

        var (visible, kind) = StreamingFenceGuard.TruncateAtOpenFence(text);

        visible.Should().Be("before\n\n");
        visible.Should().NotContain("after the fence");
        kind.Should().Be(StreamingFenceKind.Plan);
    }

    [Fact]
    public void Earliest_Fence_Wins_When_Both_Kinds_Present()
    {
        var text = "x\n:::artifact[type=\"svg\"]\n<svg/>\n:::\n:::doc-plan\n{}";

        var (visible, kind) = StreamingFenceGuard.TruncateAtOpenFence(text);

        visible.Should().Be("x\n");
        kind.Should().Be(StreamingFenceKind.Artifact);
    }

    [Theory]
    [InlineData(":::")]
    [InlineData(":::exc")]
    [InlineData(":::doc-p")]
    [InlineData(":::artifact")]
    public void Trailing_Partial_Opening_Marker_Is_Hidden(string partial)
    {
        var (visible, kind) = StreamingFenceGuard.TruncateAtOpenFence($"text so far {partial}");

        visible.Should().Be("text so far ");
        kind.Should().Be(StreamingFenceKind.None); // not classifiable yet — no chip
    }

    [Fact]
    public void Unrelated_Triple_Colon_Mid_Text_Is_Not_Hidden()
    {
        // ":::note" is not one of our fences and has more text after it —
        // only a TRAILING prefix of a real opening gets hidden.
        var text = ":::note\nsome custom container\n";

        var (visible, kind) = StreamingFenceGuard.TruncateAtOpenFence(text);

        visible.Should().Be(text);
        kind.Should().Be(StreamingFenceKind.None);
    }
}
