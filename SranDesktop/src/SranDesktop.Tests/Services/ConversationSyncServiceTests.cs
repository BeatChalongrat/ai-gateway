using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Services;

public class ConversationSyncServiceTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;

    public ConversationSyncServiceTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-sync-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
    }

    private static ConversationDto Dto(string id, string title = "t") => new(
        id, title, "gpt-4o-mini", 0,
        DateTimeOffset.UtcNow.AddHours(-1), DateTimeOffset.UtcNow,
        IsPinned: false, FolderId: null);

    [Fact]
    public async Task Sync_Upserts_Server_Conversations_And_Reconciles_Deletions()
    {
        // Local store already knows c1 (still on server) and c-gone (deleted server-side).
        await _store.UpsertConversationAsync(
            ConversationSyncService.ToEntity(Dto("c1", "old title"), DateTimeOffset.UtcNow));
        await _store.UpsertConversationAsync(
            ConversationSyncService.ToEntity(Dto("c-gone"), DateTimeOffset.UtcNow));

        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConversationListResponse(
                new List<ConversationDto> { Dto("c1", "new title"), Dto("c2") },
                HasMore: false, NextCursor: null));

        var sync = new ConversationSyncService(api.Object, _store, NullLogger<ConversationSyncService>.Instance);
        var ok = await sync.SyncFromServerAsync();

        ok.Should().BeTrue();
        var all = await _store.GetConversationsAsync();
        all.Select(c => c.Id).Should().BeEquivalentTo(new[] { "c1", "c2" });
        all.First(c => c.Id == "c1").Title.Should().Be("new title");
    }

    [Fact]
    public async Task Sync_Does_Not_Delete_Local_Conversations()
    {
        var local = ConversationSyncService.ToEntity(Dto("agent-1"), DateTimeOffset.UtcNow);
        await _store.UpsertConversationAsync(new Domain.Entities.Conversation
        {
            Id = "agent-1",
            Title = "Agent chat",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });

        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConversationListResponse(new List<ConversationDto>(), false, null));

        var sync = new ConversationSyncService(api.Object, _store, NullLogger<ConversationSyncService>.Instance);
        await sync.SyncFromServerAsync();

        (await _store.GetConversationsAsync()).Should().ContainSingle(c => c.Id == "agent-1");
    }

    [Fact]
    public async Task Sync_Pages_Through_Cursor()
    {
        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConversationListResponse(
                new List<ConversationDto> { Dto("c1") }, true, "c1"));
        api.Setup(a => a.ListConversationsAsync("c1", It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConversationListResponse(
                new List<ConversationDto> { Dto("c2") }, false, null));

        var sync = new ConversationSyncService(api.Object, _store, NullLogger<ConversationSyncService>.Instance);
        await sync.SyncFromServerAsync();

        (await _store.GetConversationsAsync()).Should().HaveCount(2);
    }

    [Fact]
    public async Task Mirror_Of_Local_Conversation_Does_Not_Duplicate_In_Sidebar()
    {
        // Phase 3 D regression: a local agent conversation was pushed to the
        // server as mirror "srv-X" — the server list now contains srv-X, and
        // reconciliation must treat it as the SAME conversation (one sidebar
        // row, the local copy staying authoritative).
        await _store.UpsertConversationAsync(new Domain.Entities.Conversation
        {
            Id = "agent-1",
            Title = "Agent chat",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });
        await _store.SetConversationServerIdAsync("agent-1", "srv-X");

        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConversationListResponse(
                new List<ConversationDto> { Dto("srv-X", "Agent chat"), Dto("srv-Y", "Web chat") },
                false, null));

        var sync = new ConversationSyncService(api.Object, _store, NullLogger<ConversationSyncService>.Instance);
        await sync.SyncFromServerAsync();

        var all = await _store.GetConversationsAsync();
        all.Select(c => c.Id).Should().BeEquivalentTo(new[] { "agent-1", "srv-Y" });
        all.First(c => c.Id == "agent-1").Source.Should().Be(ConversationSource.Local);
    }

    [Fact]
    public async Task Sync_Returns_False_When_Server_Unreachable()
    {
        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListConversationsAsync(It.IsAny<string?>(), It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new System.Net.Http.HttpRequestException("down"));

        var sync = new ConversationSyncService(api.Object, _store, NullLogger<ConversationSyncService>.Instance);

        (await sync.SyncFromServerAsync()).Should().BeFalse();
    }

    public void Dispose()
    {
        Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
        if (Directory.Exists(_dbDir))
            Directory.Delete(_dbDir, recursive: true);
    }
}
