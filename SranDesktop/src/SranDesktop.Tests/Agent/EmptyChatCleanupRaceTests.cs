using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// BUG 2 (FIX F): the empty-chat cleaner used to delete any untitled chat
/// with no stored messages, exempting only the SELECTED one. A brand-new chat
/// whose scanned-PDF attachment was still being read by the server matched
/// that description for minutes, so clicking to another chat while waiting
/// deleted it — and the reply then had nowhere to land.
///
/// These drive the real view model through its public cleanup command.
/// </summary>
public sealed class EmptyChatCleanupRaceTests
{
    private static ConversationItemViewModel Item(string id, string title = "New conversation") =>
        new(new Conversation
        {
            Id = id,
            Title = title,
            CreatedAt = DateTimeOffset.Now,
            UpdatedAt = DateTimeOffset.Now,
            Source = ConversationSource.Server,
        });

    private static (ConversationListViewModel vm, Mock<IChatEdgeApiClient> api, Mock<IConversationStore> store)
        Build(IConversationActivityTracker? tracker)
    {
        var api = new Mock<IChatEdgeApiClient>();
        var store = new Mock<IConversationStore>();
        // Every conversation looks empty — the state that made them eligible.
        store.Setup(s => s.GetMessagesAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
             .ReturnsAsync(new List<Message>());
        var settings = new Mock<ISettingsService>();
        settings.SetupGet(x => x.Current).Returns(new SranDesktop.Application.Models.UserSettings());
        var dialogs = new Mock<IDialogService>();
        dialogs.Setup(d => d.ConfirmAsync(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<bool>()))
               .ReturnsAsync(true);

        var vm = new ConversationListViewModel(
            api.Object,
            store.Object,
            Mock.Of<IConversationSyncService>(),
            Mock.Of<IAgentConversationSyncService>(),
            settings.Object,
            dialogs.Object,
            NullLogger<ConversationListViewModel>.Instance,
            tracker);
        return (vm, api, store);
    }

    [Fact]
    public async Task A_Conversation_With_An_Upload_In_Flight_Is_Not_Deleted()
    {
        var tracker = new ConversationActivityTracker();
        var (vm, api, store) = Build(tracker);
        var busy = Item("busy-conv");
        var idle = Item("idle-conv");
        vm.Conversations.Add(busy);
        vm.Conversations.Add(idle);

        using (tracker.Begin("busy-conv"))
        {
            await vm.CleanupEmptyChatsCommand.ExecuteAsync(null);
        }

        vm.Conversations.Should().Contain(busy, "its attachment was still uploading");
        api.Verify(a => a.DeleteConversationAsync("busy-conv", It.IsAny<CancellationToken>()), Times.Never);
        store.Verify(s => s.DeleteConversationAsync("busy-conv", It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Genuinely_Idle_Empty_Chats_Are_Still_Cleaned()
    {
        // The guard must not turn the feature off — that is the whole point
        // of the cleaner and it still has to work.
        var tracker = new ConversationActivityTracker();
        var (vm, api, store) = Build(tracker);
        var idle = Item("idle-conv");
        vm.Conversations.Add(idle);

        await vm.CleanupEmptyChatsCommand.ExecuteAsync(null);

        vm.Conversations.Should().NotContain(idle);
        api.Verify(a => a.DeleteConversationAsync("idle-conv", It.IsAny<CancellationToken>()), Times.Once);
        store.Verify(s => s.DeleteConversationAsync("idle-conv", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Once_The_Upload_Finishes_The_Chat_Becomes_Eligible_Again()
    {
        var tracker = new ConversationActivityTracker();
        var (vm, _, store) = Build(tracker);
        var conv = Item("conv");
        vm.Conversations.Add(conv);

        var scope = tracker.Begin("conv");
        await vm.CleanupEmptyChatsCommand.ExecuteAsync(null);
        vm.Conversations.Should().Contain(conv);

        scope.Dispose();
        await vm.CleanupEmptyChatsCommand.ExecuteAsync(null);

        vm.Conversations.Should().NotContain(conv);
        store.Verify(s => s.DeleteConversationAsync("conv", It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task A_Null_Tracker_Keeps_The_Old_Behaviour()
    {
        // The tracker is optional so existing constructions keep working;
        // without one, nothing is ever reported busy.
        var (vm, _, store) = Build(tracker: null);
        var conv = Item("conv");
        vm.Conversations.Add(conv);

        await vm.CleanupEmptyChatsCommand.ExecuteAsync(null);

        vm.Conversations.Should().NotContain(conv);
        store.Verify(s => s.DeleteConversationAsync("conv", It.IsAny<CancellationToken>()), Times.Once);
    }
}
