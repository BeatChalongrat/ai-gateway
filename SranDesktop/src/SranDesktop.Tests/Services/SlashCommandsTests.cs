using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 12 Task 1 — slash quick actions in the desktop composer, ported from
/// the web P10 menu (apps/web-chat/src/lib/slashCommands.ts).
///
/// The load-bearing assertion is the NEGATIVE one: a "/" typed inside ordinary
/// prose must never open the menu, because that would hijack normal typing —
/// a far worse regression than the feature is a win.
/// </summary>
public sealed class SlashCommandsTests
{
    [Theory]
    [InlineData("/")]
    [InlineData("/i")]
    [InlineData("/image")]
    [InlineData("/IMAGE")]
    public void Opens_For_A_Bare_Leading_Slash_Token(string text)
    {
        SlashCommands.ShouldShowMenu(text).Should().BeTrue();
    }

    [Theory]
    [InlineData("and/or")]
    [InlineData("open 24/7")]
    [InlineData("see https://example.com/docs")]
    [InlineData(" /image")]          // leading space = prose, not a command
    [InlineData("/image of a cat")]  // the token ended at the space
    [InlineData("สวัสดี/ครับ")]
    [InlineData("hello")]
    [InlineData("")]
    [InlineData("//")]
    [InlineData("/image1")]          // digits are not part of a command name
    public void Never_Opens_Mid_Sentence(string text)
    {
        SlashCommands.ShouldShowMenu(text).Should().BeFalse($"input was \"{text}\"");
    }

    [Fact]
    public void Null_Input_Is_Not_A_Command()
    {
        SlashCommands.ShouldShowMenu(null).Should().BeFalse();
        SlashCommands.Query(null).Should().BeNull();
    }

    [Fact]
    public void Query_Is_The_Lowercased_Token()
    {
        SlashCommands.Query("/").Should().Be("");
        SlashCommands.Query("/IMG").Should().Be("img");
    }

    [Fact]
    public void Empty_Query_Lists_Everything()
    {
        SlashCommands.Filter("").Should().HaveCount(SlashCommands.All.Count);
    }

    [Theory]
    [InlineData("img", SlashActionId.Image)]
    [InlineData("pic", SlashActionId.Image)]
    [InlineData("vid", SlashActionId.Video)]
    [InlineData("new", SlashActionId.NewChat)]
    [InlineData("keys", SlashActionId.Shortcuts)]
    [InlineData("help", SlashActionId.Shortcuts)]
    public void Filters_By_Keyword_And_Alias(string query, SlashActionId expected)
    {
        SlashCommands.Filter(query).Should().ContainSingle()
            .Which.Id.Should().Be(expected);
    }

    [Fact]
    public void An_Unknown_Token_Matches_Nothing()
    {
        // The menu then shows its empty state rather than disappearing
        // mid-keystroke, which would read as the app glitching.
        SlashCommands.Filter("zzz").Should().BeEmpty();
    }

    [Fact]
    public void Every_Command_Maps_To_A_Flow_This_App_Actually_Has()
    {
        // image/video = composer mode toggles, new = the sidebar's New chat,
        // shortcuts = the existing ShortcutsDialog. Nothing invented.
        SlashCommands.All.Select(c => c.Id).Should().BeEquivalentTo(new[]
        {
            SlashActionId.Image, SlashActionId.Video,
            SlashActionId.NewChat, SlashActionId.Shortcuts,
        });
    }

    [Fact]
    public void Every_Command_Has_Localized_Text_In_Both_Languages()
    {
        foreach (var cmd in SlashCommands.All)
        {
            foreach (var key in new[] { cmd.LabelKey, cmd.HintKey })
            {
                SranDesktop.Presentation.Localization.Dict.En.Should().ContainKey(key);
                SranDesktop.Presentation.Localization.Dict.Th.Should().ContainKey(key);
                SranDesktop.Presentation.Localization.Dict.En[key].Should().NotBeNullOrWhiteSpace();
                SranDesktop.Presentation.Localization.Dict.Th[key].Should().NotBeNullOrWhiteSpace();
            }
        }
    }
}
