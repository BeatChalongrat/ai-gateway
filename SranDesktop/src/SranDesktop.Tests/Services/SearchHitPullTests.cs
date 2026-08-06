using System.Net.Http;
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

/// <summary>Phase 8 Task 6: clicking a server-only search hit pulls the
/// conversation (chat-edge GET + sync-shaped upsert) and opens it; failure
/// leaves the row in place for a retry.</summary>
public class SearchHitPullTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<IConversationStore> _store = new();
    private readonly Mock<IConversationSyncService> _sync = new();
    private readonly Mock<IAgentConversationSyncService> _agentSync = new();
    private readonly Mock<ISettingsService> _settings = new();

    private ConversationListViewModel Create()
    {
        _settings.SetupGet(s => s.Current).Returns(new UserSettings());
        return new ConversationListViewModel(
            _api.Object, _store.Object, _sync.Object, _agentSync.Object,
            _settings.Object, Mock.Of<IDialogService>(), NullLogger<ConversationListViewModel>.Instance);
    }

    private static ConversationDto Dto(string id) => new(
        Id: id, Title: "Pulled chat", ModelId: "gpt-4o", MessageCount: 3,
        CreatedAt: DateTimeOffset.UtcNow.AddDays(-1),
        UpdatedAt: DateTimeOffset.UtcNow,
        IsPinned: false, ProjectId: null);

    [Fact]
    public async Task Server_Only_Hit_Pulls_Upserts_And_Opens()
    {
        _api.Setup(a => a.GetConversationAsync("srv-9", It.IsAny<CancellationToken>()))
            .ReturnsAsync(Dto("srv-9"));
        var vm = Create();
        var hit = new MessageSearchHitViewModel("srv-9", "T", "s", requiresPull: true);

        await vm.OpenMessageHitCommand.ExecuteAsync(hit);

        // Upserted through the sync entity mapping: server-source semantics.
        _store.Verify(s => s.UpsertConversationAsync(
            It.Is<Conversation>(c =>
                c.Id == "srv-9" && c.Source == ConversationSource.Server
                && c.LastSyncedAt != null),
            It.IsAny<CancellationToken>()), Times.Once);
        vm.Conversations.Should().ContainSingle(c => c.Id == "srv-9");
        vm.SelectedConversation!.Id.Should().Be("srv-9");
        hit.IsPulling.Should().BeFalse();
    }

    [Fact]
    public async Task Pull_Failure_Keeps_The_Row_And_Opens_Nothing()
    {
        _api.Setup(a => a.GetConversationAsync("srv-9", It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("down"));
        var vm = Create();
        var hit = new MessageSearchHitViewModel("srv-9", "T", "s", requiresPull: true);

        await vm.OpenMessageHitCommand.ExecuteAsync(hit); // must not throw

        vm.Conversations.Should().BeEmpty();
        vm.SelectedConversation.Should().BeNull();
        hit.IsPulling.Should().BeFalse(); // row is clickable again
        _store.Verify(s => s.UpsertConversationAsync(
            It.IsAny<Conversation>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Local_Hit_Opens_Directly_Without_Any_Network()
    {
        var vm = Create();
        var existing = new ConversationItemViewModel(new Conversation
        {
            Id = "local-1",
            Title = "Existing",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Server
        });
        vm.Conversations.Add(existing);
        var hit = new MessageSearchHitViewModel("local-1", "T", "s");

        await vm.OpenMessageHitCommand.ExecuteAsync(hit);

        vm.SelectedConversation.Should().BeSameAs(existing);
        _api.Verify(a => a.GetConversationAsync(
            It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Already_Synced_Row_Is_Not_Duplicated()
    {
        _api.Setup(a => a.GetConversationAsync("srv-9", It.IsAny<CancellationToken>()))
            .ReturnsAsync(Dto("srv-9"));
        var vm = Create();
        var hit = new MessageSearchHitViewModel("srv-9", "T", "s", requiresPull: true);
        // A concurrent sync landed the row between search and click.
        vm.Conversations.Add(new ConversationItemViewModel(new Conversation
        {
            Id = "srv-9",
            Title = "Synced meanwhile",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Server
        }));

        await vm.OpenMessageHitCommand.ExecuteAsync(hit);

        vm.Conversations.Should().ContainSingle(c => c.Id == "srv-9");
        vm.SelectedConversation!.Id.Should().Be("srv-9");
    }
}
