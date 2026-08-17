using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// The "analysis" (thinking) and "search" StreamStatusEvents were parsed by
/// ChatService but dropped by ChatViewModel's stream switch — only image/video
/// had cases, so the user saw an anonymous waiting mark while the server was
/// reasoning or web-searching. These tests pin the wiring: active status sets
/// a localized StatusText, terminal status clears it, and the image/video
/// flags keep their original behavior.
/// </summary>
public sealed class StreamStatusWiringTests
{
    private static Mock<ISettingsService> SettingsMock()
    {
        var m = new Mock<ISettingsService>();
        m.SetupGet(s => s.Current).Returns(new UserSettings());
        return m;
    }

    private static ChatViewModel CreateChat() => new(
        Mock.Of<IChatEdgeApiClient>(),
        Mock.Of<IGatewayApiClient>(),
        Mock.Of<IConversationStore>(),
        Mock.Of<IFileService>(),
        Mock.Of<IChatOrchestrator>(),
        Mock.Of<IChatService>(),
        Mock.Of<ISkillManager>(),
        Mock.Of<ISpeechService>(),
        SettingsMock().Object,
        Mock.Of<IAgentConversationSyncService>(),
        NullLogger<ChatViewModel>.Instance);

    private static ConversationItemViewModel Conversation() =>
        new(new Conversation { Id = "c1", CreatedAt = DateTimeOffset.UtcNow });

    private static MessageViewModel Bubble() =>
        new("m1", MessageRole.Assistant, "", DateTimeOffset.UtcNow)
        {
            IsStreaming = true,
        };

    [Theory]
    [InlineData("analysis")]
    [InlineData("search")]
    public async Task Active_Analysis_And_Search_Set_StatusText(string kind)
    {
        var vm = CreateChat();
        var bubble = Bubble();

        await vm.OnReattachEventAsync(Conversation(), bubble,
            new StreamStatusEvent(kind, "started"), CancellationToken.None);

        bubble.StatusText.Should().NotBeNullOrEmpty(
            "the phase marker must surface as a visible status label");
    }

    [Theory]
    [InlineData("analysis")]
    [InlineData("search")]
    public async Task Completed_Status_Clears_StatusText(string kind)
    {
        var vm = CreateChat();
        var bubble = Bubble();

        await vm.OnReattachEventAsync(Conversation(), bubble,
            new StreamStatusEvent(kind, "started"), CancellationToken.None);
        await vm.OnReattachEventAsync(Conversation(), bubble,
            new StreamStatusEvent(kind, "completed"), CancellationToken.None);

        bubble.StatusText.Should().BeNull();
    }

    [Fact]
    public async Task Image_Status_Behavior_Is_Unchanged()
    {
        var vm = CreateChat();
        var bubble = Bubble();

        await vm.OnReattachEventAsync(Conversation(), bubble,
            new StreamStatusEvent("image", "generating"), CancellationToken.None);
        bubble.IsGeneratingImage.Should().BeTrue();
        bubble.StatusText.Should().BeNull("image uses its own placeholder, not StatusText");

        await vm.OnReattachEventAsync(Conversation(), bubble,
            new StreamStatusEvent("image", "completed"), CancellationToken.None);
        bubble.IsGeneratingImage.Should().BeFalse();
    }
}
