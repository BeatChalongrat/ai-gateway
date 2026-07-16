using FluentAssertions;
using SranDesktop.Presentation.Services;
using Xunit;

namespace SranDesktop.Tests.Rendering;

/// <summary>Phase C PART 2: jump-to-latest visibility predicate (the scroll
/// action itself is a visual check).</summary>
public class ScrollStateHelperTests
{
    [Theory]
    // pinned, content-below → hidden (already at bottom)
    [InlineData(true, true, false)]
    [InlineData(true, false, false)]
    // scrolled up with content below → SHOW
    [InlineData(false, true, true)]
    // scrolled up but nothing below (short chat) → hidden
    [InlineData(false, false, false)]
    public void ShouldShow_Matches_The_States(bool pinned, bool hasBelow, bool expected)
    {
        ScrollStateHelper.ShouldShowJumpToLatest(pinned, hasBelow).Should().Be(expected);
    }
}
