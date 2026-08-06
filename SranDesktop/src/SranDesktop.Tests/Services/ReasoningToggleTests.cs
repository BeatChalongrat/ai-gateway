using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 13 Task 1 — the composer's Thinking-Mode button (web parity P11).
///
/// The interesting rules are all about WHEN THE BUTTON IS ABSENT. A toggle
/// that appears but does nothing is worse than no toggle at all, so it is
/// hidden on Auto (the router may land on a model that cannot think) and on
/// any model the catalogue doesn't advertise as reasoning-capable.
/// </summary>
public sealed class ReasoningToggleTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();

    private static ModelInfo Model(string id, bool reasoning) =>
        new(id, id, "openai", "chat", true,
            Reasoning: reasoning ? new ReasoningCapability(true, null, null, false) : null);

    private ChatViewModel Create()
    {
        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());
        return new ChatViewModel(
            _api.Object,
            Mock.Of<IGatewayApiClient>(),
            Mock.Of<IConversationStore>(),
            Mock.Of<IFileService>(),
            Mock.Of<IChatOrchestrator>(),
            Mock.Of<IChatService>(),
            Mock.Of<ISkillManager>(),
            Mock.Of<ISpeechService>(),
            settings.Object,
            Mock.Of<IAgentConversationSyncService>(),
            NullLogger<ChatViewModel>.Instance);
    }

    private static ConversationItemViewModel Conversation(string modelId) =>
        new(new Conversation
        {
            Id = "c1",
            Title = "chat",
            ModelId = modelId,
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        });

    [Fact]
    public void Shown_For_A_Reasoning_Capable_Model()
    {
        var vm = Create();
        vm.AvailableModels.Add(Model("o4-mini", reasoning: true));
        vm.Conversation = Conversation("o4-mini");

        vm.CanToggleReasoning.Should().BeTrue();
    }

    [Fact]
    public void Hidden_For_A_Model_That_Cannot_Think()
    {
        var vm = Create();
        vm.AvailableModels.Add(Model("gpt-4o-mini", reasoning: false));
        vm.Conversation = Conversation("gpt-4o-mini");

        vm.CanToggleReasoning.Should().BeFalse();
    }

    [Fact]
    public void Hidden_On_Auto_Even_Though_The_Router_Might_Pick_A_Thinker()
    {
        var vm = Create();
        vm.AvailableModels.Add(Model("o4-mini", reasoning: true));
        vm.Conversation = Conversation("auto");

        vm.IsAutoSelected.Should().BeTrue();
        vm.CanToggleReasoning.Should().BeFalse();
    }

    [Fact]
    public void Hidden_When_The_Catalogue_Has_Not_Loaded()
    {
        // Unknown model = treated as "cannot think". Asking a non-reasoning
        // model to think is an API error, not a slightly worse answer.
        var vm = Create();
        vm.Conversation = Conversation("some-model");

        vm.CanToggleReasoning.Should().BeFalse();
    }

    [Fact]
    public void Hidden_On_The_Home_Screen()
    {
        var vm = Create();
        vm.AvailableModels.Add(Model("o4-mini", reasoning: true));

        vm.Conversation.Should().BeNull();
        vm.CanToggleReasoning.Should().BeFalse("the flag is per-conversation");
    }

    [Fact]
    public async Task Toggling_Writes_The_Conversation_Flag()
    {
        var vm = Create();
        vm.AvailableModels.Add(Model("o4-mini", reasoning: true));
        vm.Conversation = Conversation("o4-mini");

        await vm.ToggleReasoningCommand.ExecuteAsync(null);

        vm.IsReasoningOn.Should().BeTrue();
        _api.Verify(a => a.SetConversationReasoningAsync("c1", true, It.IsAny<CancellationToken>()), Times.Once);

        await vm.ToggleReasoningCommand.ExecuteAsync(null);

        vm.IsReasoningOn.Should().BeFalse();
        _api.Verify(a => a.SetConversationReasoningAsync("c1", false, It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task A_Rejected_Toggle_Reverts_So_The_Button_Never_Lies()
    {
        _api.Setup(a => a.SetConversationReasoningAsync(
                It.IsAny<string>(), It.IsAny<bool>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));
        var vm = Create();
        vm.Conversation = Conversation("o4-mini");

        await vm.ToggleReasoningCommand.ExecuteAsync(null);

        vm.IsReasoningOn.Should().BeFalse();
        vm.ErrorMessage.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task Toggling_Without_A_Conversation_Is_A_No_Op()
    {
        var vm = Create();

        await vm.ToggleReasoningCommand.ExecuteAsync(null);

        _api.Verify(a => a.SetConversationReasoningAsync(
            It.IsAny<string>(), It.IsAny<bool>(), It.IsAny<CancellationToken>()), Times.Never);
    }
}
