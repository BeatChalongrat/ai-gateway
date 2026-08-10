using FluentAssertions;
using SranDesktop.Presentation.Services;
using Xunit;

namespace SranDesktop.Tests.Presentation;

/// <summary>The composer's Ctrl+V precedence. The handler owns the WPF
/// Clipboard calls (untestable); this is the decision it delegates to.</summary>
public class ClipboardPasteDeciderTests
{
    [Theory]
    [InlineData(true, true, PasteMode.Image)]   // browser/Word copy: image + HTML/text alternative
    [InlineData(true, false, PasteMode.Image)]  // bitmap only
    [InlineData(false, true, PasteMode.Text)]   // plain text
    [InlineData(false, false, PasteMode.Text)]  // nothing usable — TextBox handles it
    public void Decide(bool hasImage, bool hasText, PasteMode expected)
    {
        ClipboardPasteDecider.Decide(hasImage, hasText).Should().Be(expected);
    }

    [Fact]
    public void Image_Wins_Over_Text_When_Both_Are_Present()
    {
        // Regression: the old handler required !ContainsText(), so a clipboard
        // carrying both silently dropped the image.
        ClipboardPasteDecider.Decide(hasImage: true, hasText: true)
            .Should().Be(PasteMode.Image);
    }
}
