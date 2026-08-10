using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 13 Task 3 — when the loading skeletons are visible.
///
/// The whole point is the SECOND condition. "Loading" alone is not enough:
/// paging older messages, or a background sidebar refresh, would then cover
/// content the user is already reading with grey bars, which reads as the app
/// having lost the conversation.
/// </summary>
public sealed class SkeletonStateTests
{
    private static Mock<ISettingsService> SettingsMock()
    {
        var m = new Mock<ISettingsService>();
        m.SetupGet(s => s.Current).Returns(new UserSettings());
        return m;
    }

    // ── Transcript skeleton ────────────────────────────────────────────────

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

    [Fact]
    public void Transcript_Skeleton_Shows_While_An_Empty_Chat_Loads()
    {
        var vm = CreateChat();

        vm.ShowHistorySkeleton.Should().BeFalse("nothing is loading yet");
        vm.IsLoadingHistory = true;
        vm.ShowHistorySkeleton.Should().BeTrue();
    }

    [Fact]
    public void Transcript_Skeleton_Disappears_As_Soon_As_A_Message_Arrives()
    {
        var vm = CreateChat();
        vm.IsLoadingHistory = true;

        vm.Messages.Add(new MessageViewModel("m1", MessageRole.Assistant, "hi", DateTimeOffset.UtcNow));

        vm.ShowHistorySkeleton.Should().BeFalse(
            "paging must not hide messages the user can already read");
    }

    [Fact]
    public void Transcript_Skeleton_Raises_Change_Notifications()
    {
        var vm = CreateChat();
        var raised = new List<string?>();
        vm.PropertyChanged += (_, e) => raised.Add(e.PropertyName);

        vm.IsLoadingHistory = true;
        vm.Messages.Add(new MessageViewModel("m2", MessageRole.User, "hello", DateTimeOffset.UtcNow));

        raised.Should().Contain(nameof(ChatViewModel.ShowHistorySkeleton));
        raised.Count(n => n == nameof(ChatViewModel.ShowHistorySkeleton))
            .Should().BeGreaterThanOrEqualTo(2, "both the flag and the list drive it");
    }

    [Fact]
    public void No_Skeleton_When_Loading_Finishes()
    {
        var vm = CreateChat();
        vm.IsLoadingHistory = true;
        vm.IsLoadingHistory = false;

        vm.ShowHistorySkeleton.Should().BeFalse();
    }

    // ── Sidebar skeleton ───────────────────────────────────────────────────

    private static ConversationListViewModel CreateList() => new(
        Mock.Of<IChatEdgeApiClient>(),
        Mock.Of<IConversationStore>(),
        Mock.Of<IConversationSyncService>(),
        Mock.Of<IAgentConversationSyncService>(),
        SettingsMock().Object,
        Mock.Of<IDialogService>(),
        NullLogger<ConversationListViewModel>.Instance);

    [Fact]
    public void Sidebar_Skeleton_Shows_On_A_Cold_Start()
    {
        var vm = CreateList();

        vm.ShowListSkeleton.Should().BeFalse();
        vm.IsLoading = true;
        vm.ShowListSkeleton.Should().BeTrue();
    }

    [Fact]
    public void Sidebar_Skeleton_Hides_Once_There_Are_Rows()
    {
        var vm = CreateList();
        vm.IsLoading = true;

        vm.Conversations.Add(new ConversationItemViewModel(new Conversation
        {
            Id = "c1",
            Title = "Existing chat",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        }));
        vm.RebuildSidebarRowsNow();

        vm.SidebarRows.Should().NotBeEmpty();
        vm.ShowListSkeleton.Should().BeFalse(
            "a background refresh must not blank out the user's chat list");
    }

    [Fact]
    public void Rebuilding_Rows_Notifies_The_Skeleton_Binding()
    {
        // The in-place reconcile can change the row count without a collection
        // event the binding would pick up on its own.
        var vm = CreateList();
        vm.IsLoading = true;
        var raised = new List<string?>();
        vm.PropertyChanged += (_, e) => raised.Add(e.PropertyName);

        vm.RebuildSidebarRowsNow();

        raised.Should().Contain(nameof(ConversationListViewModel.ShowListSkeleton));
    }
}
