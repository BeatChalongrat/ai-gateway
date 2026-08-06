using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 13 Task 1 — account-wide custom instructions on the desktop.
///
/// Two things are actually at stake and both are asserted here:
///
///  1. The desktop must render the SAME prompt block the Go prompt compiler
///     renders (preferences_service.go BuildInstructionsBlock). If the two
///     drift, one chat obeys the user's persona and the other doesn't —
///     which reads as the AI randomly ignoring settings.
///
///  2. A failed READ must never turn into a WRITE of empty strings. The
///     fields are shared with the web, so saving a blank form the user never
///     filled in would delete instructions they set elsewhere.
/// </summary>
public sealed class PersonalInstructionsTests
{
    // ── The prompt block (parity with the server renderer) ─────────────────

    [Fact]
    public void Renders_Both_Sections_In_The_Server_Order()
    {
        var block = PersonalInstructions.BuildPromptBlock("I'm a Thai dev", "Be terse");

        block.Should().Be("## About the user\nI'm a Thai dev\n\n## Response style\nBe terse");
    }

    [Fact]
    public void Omits_The_Section_Whose_Field_Is_Empty()
    {
        PersonalInstructions.BuildPromptBlock("just about", null)
            .Should().Be("## About the user\njust about");
        PersonalInstructions.BuildPromptBlock("   ", "just style")
            .Should().Be("## Response style\njust style");
    }

    [Theory]
    [InlineData(null, null)]
    [InlineData("", "")]
    [InlineData("   ", "\n\t ")]
    public void Nothing_Set_Renders_Nothing(string? about, string? respond)
    {
        // Callers append unconditionally, so "" is the contract — not a
        // heading with an empty body, which would confuse the model.
        PersonalInstructions.BuildPromptBlock(about, respond).Should().BeEmpty();
    }

    [Fact]
    public void Enforces_The_Same_Cap_The_Server_Does()
    {
        var ok = new string('x', PersonalInstructions.MaxChars);
        var over = ok + "x";
        PersonalInstructions.IsTooLong(ok, ok).Should().BeFalse();
        PersonalInstructions.IsTooLong(over, "").Should().BeTrue();
        PersonalInstructions.IsTooLong("", over).Should().BeTrue();
    }

    // ── The session cache ──────────────────────────────────────────────────

    [Fact]
    public void The_Cache_Round_Trips_And_Clears()
    {
        try
        {
            PersonalInstructionsState.Set("A", "B");
            PersonalInstructionsState.About.Should().Be("A");
            PersonalInstructionsState.Respond.Should().Be("B");
            PersonalInstructionsState.PromptBlock().Should().Contain("## About the user");

            PersonalInstructionsState.Clear();
            PersonalInstructionsState.PromptBlock().Should().BeEmpty();
        }
        finally
        {
            PersonalInstructionsState.Clear();
        }
    }

    // ── The Settings editor ────────────────────────────────────────────────

    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<ISettingsService> _settings = new();

    private SettingsViewModel CreateSettings()
    {
        _settings.SetupGet(s => s.Current).Returns(new UserSettings());
        var list = new ConversationListViewModel(
            _api.Object, Mock.Of<IConversationStore>(), Mock.Of<IConversationSyncService>(),
            Mock.Of<IAgentConversationSyncService>(), _settings.Object, Mock.Of<IDialogService>(),
            NullLogger<ConversationListViewModel>.Instance);
        return new SettingsViewModel(
            _settings.Object, _api.Object, Mock.Of<IStartupManager>(), list,
            Mock.Of<IBrowserBridgeService>(), Mock.Of<IBrowserExtensionInstaller>(),
            NullLogger<SettingsViewModel>.Instance);
    }

    [Fact]
    public async Task Loads_The_Two_Fields_From_The_Shared_Endpoint()
    {
        _api.Setup(a => a.GetPreferencesAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new UserPreferencesDto("about me", "be brief"));
        var vm = CreateSettings();

        await vm.LoadPersonalInstructionsAsync();

        vm.AboutYou.Should().Be("about me");
        vm.RespondStyle.Should().Be("be brief");
        vm.PersonalInstructionsStatus.Should().BeNull();
    }

    [Fact]
    public async Task A_Failed_Load_Says_So_And_Leaves_The_Fields_Empty()
    {
        _api.Setup(a => a.GetPreferencesAsync(It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));
        var vm = CreateSettings();

        await vm.LoadPersonalInstructionsAsync();

        vm.AboutYou.Should().BeEmpty();
        vm.PersonalInstructionsStatus.Should().NotBeNullOrEmpty(
            "a silent empty form invites the user to save over their web-set text");
    }

    [Fact]
    public async Task Saves_Both_Fields_Together_Through_The_Web_Endpoint()
    {
        _api.Setup(a => a.GetPreferencesAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new UserPreferencesDto("", ""));
        _api.Setup(a => a.UpdatePreferencesAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string a, string r, CancellationToken _) => new UserPreferencesDto(a, r));
        var vm = CreateSettings();
        await vm.LoadPersonalInstructionsAsync();
        vm.AboutYou = "ผมเป็นคนไทย";
        vm.RespondStyle = "ตอบสั้น ๆ";

        try
        {
            await vm.SavePersonalInstructionsCommand.ExecuteAsync(null);

            _api.Verify(a => a.UpdatePreferencesAsync(
                "ผมเป็นคนไทย", "ตอบสั้น ๆ", It.IsAny<CancellationToken>()), Times.Once);
            PersonalInstructionsState.About.Should().Be("ผมเป็นคนไทย");
        }
        finally
        {
            PersonalInstructionsState.Clear();
        }
    }

    [Fact]
    public async Task An_Over_Length_Field_Is_Refused_Before_It_Is_Sent()
    {
        var vm = CreateSettings();
        vm.AboutYou = new string('x', PersonalInstructions.MaxChars + 1);

        vm.IsPersonalTooLong.Should().BeTrue();
        await vm.SavePersonalInstructionsCommand.ExecuteAsync(null);

        _api.Verify(a => a.UpdatePreferencesAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        vm.PersonalInstructionsStatus.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task A_Failed_Save_Does_Not_Update_The_Prompt_Cache()
    {
        _api.Setup(a => a.UpdatePreferencesAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("500"));
        var vm = CreateSettings();
        vm.AboutYou = "never accepted";

        try
        {
            await vm.SavePersonalInstructionsCommand.ExecuteAsync(null);

            PersonalInstructionsState.About.Should().NotBe("never accepted");
            vm.PersonalInstructionsStatus.Should().NotBeNullOrEmpty();
            vm.CanSavePersonalInstructions.Should().BeTrue("the editor must stay usable");
        }
        finally
        {
            PersonalInstructionsState.Clear();
        }
    }

    [Fact]
    public void Character_Counters_Track_The_Editors()
    {
        var vm = CreateSettings();
        vm.AboutYou = "abc";
        vm.RespondStyle = "de";

        vm.AboutYouLength.Should().Be(3);
        vm.RespondStyleLength.Should().Be(2);
        vm.PersonalInstructionsMax.Should().Be(PersonalInstructions.MaxChars);
    }

    [Fact]
    public void Every_String_Exists_In_Both_Languages()
    {
        foreach (var key in new[]
                 {
                     "settings.personal", "settings.personal.subtitle",
                     "settings.personal.about", "settings.personal.respond",
                     "settings.personal.saved", "settings.personal.saveFailed",
                     "settings.personal.loadFailed", "settings.personal.tooLong",
                     "input.reasoning",
                 })
        {
            SranDesktop.Presentation.Localization.Dict.En.Should().ContainKey(key);
            SranDesktop.Presentation.Localization.Dict.Th.Should().ContainKey(key);
            SranDesktop.Presentation.Localization.Dict.Th[key].Should().NotBeNullOrWhiteSpace();
        }
    }
}
