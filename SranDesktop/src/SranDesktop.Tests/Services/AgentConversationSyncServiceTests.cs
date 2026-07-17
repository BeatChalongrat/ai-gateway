using System.Net;
using System.Net.Http;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Services;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Phase 3 D: one-way push sync against a REAL temp SQLite store and
/// a mocked chat-edge client.</summary>
public class AgentConversationSyncServiceTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<ISettingsService> _settings = new();
    private readonly AgentConversationSyncService _sync;

    private static ConversationDto ServerDto(string id) => new(
        id, "Agent chat", "gpt-4o-mini", 0,
        DateTimeOffset.UtcNow.AddMinutes(-10), DateTimeOffset.UtcNow, false, null);

    public AgentConversationSyncServiceTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-sync-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
        _settings.SetupGet(s => s.Current).Returns(new UserSettings { SyncAgentConversations = true });
        _sync = new AgentConversationSyncService(
            _api.Object, _store, _settings.Object,
            NullLogger<AgentConversationSyncService>.Instance);
    }

    private async Task SeedLocalConversationAsync(
        string id = "local-1", int messages = 2, DateTimeOffset? updatedAt = null)
    {
        await _store.UpsertConversationAsync(new Conversation
        {
            Id = id,
            Title = "Agent chat",
            ModelId = "gpt-4o-mini",
            CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-10),
            // Explicit UpdatedAt makes the sweep order deterministic
            // (GetConversationsAsync sorts updated_at DESC).
            UpdatedAt = updatedAt ?? DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });
        var rows = new List<Message>();
        for (var i = 0; i < messages; i++)
        {
            rows.Add(new Message
            {
                Id = $"{id}-m{i}",
                ConversationId = id,
                Role = i % 2 == 0 ? MessageRole.User : MessageRole.Assistant,
                Content = $"message {i}",
                CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-9 + i),
                SortOrder = i
            });
        }
        await _store.UpsertMessagesAsync(rows);
    }

    private void SetupHappyApi(string serverId = "srv-1")
    {
        _api.Setup(a => a.ImportConversationAsync(
                It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ImportConversationResponse(ServerDto(serverId), true));
        _api.Setup(a => a.ImportMessagesAsync(
                It.IsAny<string>(), It.IsAny<IReadOnlyList<ImportMessageItem>>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string _, IReadOnlyList<ImportMessageItem> items, CancellationToken _) =>
                new ImportMessagesResponse(items.Count, 0));
    }

    [Fact]
    public async Task First_Sync_Imports_Conversation_And_Messages_And_Marks_Them()
    {
        await SeedLocalConversationAsync();
        SetupHappyApi();

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportConversationAsync(
            "local-1", "Agent chat", "gpt-4o-mini", It.IsAny<DateTimeOffset>(),
            It.IsAny<CancellationToken>()), Times.Once);
        _api.Verify(a => a.ImportMessagesAsync(
            "srv-1",
            It.Is<IReadOnlyList<ImportMessageItem>>(items =>
                items.Count == 2 && items[0].ClientId == "local-1-m0" && items[0].Role == "user"
                && items[1].ClientId == "local-1-m1" && items[1].Role == "assistant"),
            It.IsAny<CancellationToken>()), Times.Once);

        (await _store.GetConversationAsync("local-1"))!.ServerId.Should().Be("srv-1");
        (await _store.GetUnsyncedMessagesAsync("local-1")).Should().BeEmpty();
    }

    [Fact]
    public async Task Second_Sync_Sends_Nothing()
    {
        await SeedLocalConversationAsync();
        SetupHappyApi();
        await _sync.SyncAllAsync();
        _api.Invocations.Clear();

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportConversationAsync(
            It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
            It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Never);
        _api.Verify(a => a.ImportMessagesAsync(
            It.IsAny<string>(), It.IsAny<IReadOnlyList<ImportMessageItem>>(),
            It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task New_Turn_Syncs_Only_The_New_Messages()
    {
        await SeedLocalConversationAsync();
        SetupHappyApi();
        await _sync.SyncAllAsync();
        _api.Invocations.Clear();

        await _store.UpsertMessagesAsync(new[]
        {
            new Message { Id = "local-1-m2", ConversationId = "local-1", Role = MessageRole.User,
                Content = "next turn", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 2 },
            new Message { Id = "local-1-m3", ConversationId = "local-1", Role = MessageRole.Assistant,
                Content = "next reply", CreatedAt = DateTimeOffset.UtcNow.AddSeconds(1), SortOrder = 3 },
        });

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportMessagesAsync(
            "srv-1",
            It.Is<IReadOnlyList<ImportMessageItem>>(items =>
                items.Count == 2 && items[0].ClientId == "local-1-m2" && items[1].ClientId == "local-1-m3"),
            It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Tool_And_Empty_Messages_Never_Sync()
    {
        await SeedLocalConversationAsync(messages: 1);
        await _store.UpsertMessagesAsync(new[]
        {
            new Message { Id = "tool-1", ConversationId = "local-1", Role = MessageRole.Tool,
                ToolCallId = "c1", Name = "read_file", Content = "tool result",
                CreatedAt = DateTimeOffset.UtcNow, SortOrder = 5 },
            new Message { Id = "empty-1", ConversationId = "local-1", Role = MessageRole.Assistant,
                Content = "", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 6 },
        });
        SetupHappyApi();

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportMessagesAsync(
            "srv-1",
            It.Is<IReadOnlyList<ImportMessageItem>>(items =>
                items.Count == 1 && items[0].ClientId == "local-1-m0"),
            It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Offline_Leaves_State_Intact_And_Next_Sync_Succeeds()
    {
        await SeedLocalConversationAsync();
        _api.Setup(a => a.ImportConversationAsync(
                It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));

        var act = () => _sync.SyncAllAsync();
        await act.Should().NotThrowAsync();

        (await _store.GetConversationAsync("local-1"))!.ServerId.Should().BeNull();
        (await _store.GetUnsyncedMessagesAsync("local-1")).Should().HaveCount(2);

        // Server comes back — the retry trigger completes the sync.
        SetupHappyApi();
        await _sync.SyncAllAsync();
        (await _store.GetConversationAsync("local-1"))!.ServerId.Should().Be("srv-1");
        (await _store.GetUnsyncedMessagesAsync("local-1")).Should().BeEmpty();
    }

    [Fact]
    public async Task Setting_Off_Makes_Zero_Api_Calls()
    {
        await SeedLocalConversationAsync();
        _settings.SetupGet(s => s.Current)
            .Returns(new UserSettings { SyncAgentConversations = false });
        SetupHappyApi();

        await _sync.SyncAllAsync();
        _sync.QueueConversationSync("local-1");

        _api.Invocations.Should().BeEmpty();
    }

    // ── FIX 9 Part A: server-created conversations push via their own id ────

    [Fact]
    public async Task Server_Source_With_Nothing_Unsynced_Makes_No_Calls()
    {
        await _store.UpsertConversationAsync(new Conversation
        {
            Id = "server-1", Title = "Web chat", CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow, Source = ConversationSource.Server
        });
        SetupHappyApi();

        await _sync.SyncAllAsync();

        _api.Invocations.Should().BeEmpty();
    }

    private async Task SeedServerConversationAsync(string id = "srv-conv-1", int messages = 2)
    {
        await _store.UpsertConversationAsync(new Conversation
        {
            Id = id, Title = "Agent chat", ModelId = "gpt-4o-mini",
            CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-10),
            UpdatedAt = DateTimeOffset.UtcNow, Source = ConversationSource.Server
        });
        var rows = new List<Message>();
        for (var i = 0; i < messages; i++)
        {
            rows.Add(new Message
            {
                Id = $"{id}-m{i}",
                ConversationId = id,
                Role = i % 2 == 0 ? MessageRole.User : MessageRole.Assistant,
                Content = $"message {i}",
                CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-9 + i),
                SortOrder = i
            });
        }
        await _store.UpsertMessagesAsync(rows);
    }

    [Fact]
    public async Task Server_Source_Pushes_Unsynced_Rows_Via_Its_Own_Id_Without_Import_Ensure()
    {
        // FIX 9: conversations are created through the server API, so their
        // local id IS the server conversation id — the agent turn must land
        // there directly, with no ImportConversation mirror and no server_id
        // mapping (that would make the sidebar pull mistake it for a mirror).
        await SeedServerConversationAsync();
        SetupHappyApi();

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportConversationAsync(
            It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
            It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Never);
        _api.Verify(a => a.ImportMessagesAsync(
            "srv-conv-1",
            It.Is<IReadOnlyList<ImportMessageItem>>(items =>
                items.Count == 2 && items[0].ClientId == "srv-conv-1-m0" && items[0].Role == "user"
                && items[1].ClientId == "srv-conv-1-m1" && items[1].Role == "assistant"),
            It.IsAny<CancellationToken>()), Times.Once);
        (await _store.GetUnsyncedMessagesAsync("srv-conv-1")).Should().BeEmpty();
        (await _store.GetConversationAsync("srv-conv-1"))!.ServerId.Should().BeNull();
    }

    [Fact]
    public async Task Agent_Tool_Chain_Rows_Never_Sync()
    {
        // The agent's working state is local-only: an assistant preamble that
        // carries tool_calls and a screenshot placeholder (role user, marked
        // by name) must never reach the server transcript even though their
        // roles pass the naive user/assistant filter.
        await SeedServerConversationAsync(messages: 1);
        await _store.UpsertMessagesAsync(new[]
        {
            new Message { Id = "chain-a", ConversationId = "srv-conv-1", Role = MessageRole.Assistant,
                Content = "Let me look at that file.", ToolCallsJson = "[{\"id\":\"c1\"}]",
                CreatedAt = DateTimeOffset.UtcNow, SortOrder = 5 },
            new Message { Id = "chain-s", ConversationId = "srv-conv-1", Role = MessageRole.User,
                Content = Application.Services.AgentChainCodec.ScreenshotPlaceholderText,
                Name = Application.Services.AgentChainCodec.ScreenshotMarker,
                CreatedAt = DateTimeOffset.UtcNow, SortOrder = 6 },
        });
        SetupHappyApi();

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportMessagesAsync(
            "srv-conv-1",
            It.Is<IReadOnlyList<ImportMessageItem>>(items =>
                items.Count == 1 && items[0].ClientId == "srv-conv-1-m0"),
            It.IsAny<CancellationToken>()), Times.Once);
    }

    // ── FIX 9 Part A: awaited per-turn landing ──────────────────────────────

    [Fact]
    public async Task SyncNow_Lands_The_Turn_And_Returns_True()
    {
        await SeedServerConversationAsync();
        SetupHappyApi();

        var landed = await _sync.SyncConversationNowAsync("srv-conv-1");

        landed.Should().BeTrue();
        (await _store.GetUnsyncedMessagesAsync("srv-conv-1")).Should().BeEmpty();
    }

    [Fact]
    public async Task SyncNow_Offline_Returns_False_And_Requeues_For_Retry()
    {
        await SeedServerConversationAsync();
        _api.Setup(a => a.ImportMessagesAsync(
                It.IsAny<string>(), It.IsAny<IReadOnlyList<ImportMessageItem>>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));

        var landed = await _sync.SyncConversationNowAsync("srv-conv-1");

        landed.Should().BeFalse();
        _sync.PendingQueueSnapshot.Should().Contain("srv-conv-1");
        (await _store.GetUnsyncedMessagesAsync("srv-conv-1")).Should().HaveCount(2);

        // Server comes back — the queued retry completes the landing.
        SetupHappyApi();
        await _sync.DrainQueueAsync();
        (await _store.GetUnsyncedMessagesAsync("srv-conv-1")).Should().BeEmpty();
    }

    [Fact]
    public async Task SyncNow_With_Sync_Disabled_Reports_Nothing_Pending()
    {
        await SeedServerConversationAsync();
        _settings.SetupGet(s => s.Current)
            .Returns(new UserSettings { SyncAgentConversations = false });

        (await _sync.SyncConversationNowAsync("srv-conv-1")).Should().BeTrue();
        _api.Invocations.Should().BeEmpty();
    }

    // ── FIX 9 Part C: a delete on any client stays deleted ──────────────────

    private static HttpRequestException Http(HttpStatusCode status) =>
        new("server answered", null, status);

    [Fact]
    public async Task Remote_Delete_Propagates_Locally_Instead_Of_Resurrecting()
    {
        // The conversation was deleted in web-chat. The push discovers the 404
        // and must DELETE the local copy + raise the event — never re-import
        // (the old recovery resurrected user-deleted conversations).
        await SeedServerConversationAsync();
        _api.Setup(a => a.ImportMessagesAsync(
                It.IsAny<string>(), It.IsAny<IReadOnlyList<ImportMessageItem>>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(Http(HttpStatusCode.NotFound));
        string? deletedId = null;
        _sync.RemoteConversationDeleted += (_, id) => deletedId = id;

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportConversationAsync(
            It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
            It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Never);
        (await _store.GetConversationAsync("srv-conv-1")).Should().BeNull();
        deletedId.Should().Be("srv-conv-1");
    }

    [Fact]
    public async Task Remote_Delete_Of_Legacy_Mirror_Also_Propagates()
    {
        // Legacy local-first conversation whose Phase 3 mirror was deleted on
        // the web: same outcome — local delete, no resurrection.
        await SeedLocalConversationAsync();
        await _store.SetConversationServerIdAsync("local-1", "srv-old");
        _api.Setup(a => a.ImportMessagesAsync(
                "srv-old", It.IsAny<IReadOnlyList<ImportMessageItem>>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(Http(HttpStatusCode.NotFound));
        string? deletedId = null;
        _sync.RemoteConversationDeleted += (_, id) => deletedId = id;

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportConversationAsync(
            It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
            It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Never);
        (await _store.GetConversationAsync("local-1")).Should().BeNull();
        deletedId.Should().Be("local-1");
    }

    [Fact]
    public async Task Gone_On_First_Import_Propagates_The_Delete_Too()
    {
        // First push of a legacy local conversation whose client_id maps to a
        // conversation the user already deleted: the server answers 410 Gone
        // (it no longer resurrects) and the local copy must go away.
        await SeedLocalConversationAsync();
        _api.Setup(a => a.ImportConversationAsync(
                "local-1", It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(Http(HttpStatusCode.Gone));
        string? deletedId = null;
        _sync.RemoteConversationDeleted += (_, id) => deletedId = id;

        await _sync.SyncAllAsync();

        (await _store.GetConversationAsync("local-1")).Should().BeNull();
        deletedId.Should().Be("local-1");
    }

    [Fact]
    public async Task One_Poisoned_Conversation_Does_Not_Starve_The_Sweep()
    {
        // "bad-1" is newer → swept first; its 422 must not block "good-1".
        await SeedLocalConversationAsync("bad-1", updatedAt: DateTimeOffset.UtcNow);
        await SeedLocalConversationAsync("good-1", updatedAt: DateTimeOffset.UtcNow.AddMinutes(-1));

        _api.Setup(a => a.ImportConversationAsync(
                "bad-1", It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(Http(HttpStatusCode.UnprocessableEntity));
        _api.Setup(a => a.ImportConversationAsync(
                "good-1", It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ImportConversationResponse(ServerDto("srv-good"), true));
        _api.Setup(a => a.ImportMessagesAsync(
                "srv-good", It.IsAny<IReadOnlyList<ImportMessageItem>>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string _, IReadOnlyList<ImportMessageItem> items, CancellationToken _) =>
                new ImportMessagesResponse(items.Count, 0));

        await _sync.SyncAllAsync();

        (await _store.GetConversationAsync("good-1"))!.ServerId.Should().Be("srv-good");
        (await _store.GetUnsyncedMessagesAsync("good-1")).Should().BeEmpty();
        (await _store.GetConversationAsync("bad-1"))!.ServerId.Should().BeNull();
    }

    // ── Phase 10 D3: an offline drain must not strand queued ids ────────────

    [Fact]
    public async Task Offline_Drain_ReQueues_Undrained_Ids_And_Next_Drain_Syncs_Them()
    {
        await SeedLocalConversationAsync("q-1");
        await SeedLocalConversationAsync("q-2");
        await SeedLocalConversationAsync("q-3");

        // Transport failure (no StatusCode) on EVERY push — fully offline.
        _api.Setup(a => a.ImportConversationAsync(
                It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));

        _sync.QueueConversationSync("q-1");
        _sync.QueueConversationSync("q-2");
        _sync.QueueConversationSync("q-3");
        await _sync.DrainQueueAsync(); // deterministic drain (bypasses the 5s debounce)

        // The drain stopped at the first unreachable push — every id it did
        // not complete must still be pending, not lost until an app restart.
        _sync.PendingQueueSnapshot.Should().BeEquivalentTo(new[] { "q-1", "q-2", "q-3" });

        // Server comes back — the next drain pushes all of them.
        SetupHappyApi();
        await _sync.DrainQueueAsync();

        _sync.PendingQueueSnapshot.Should().BeEmpty();
        foreach (var id in new[] { "q-1", "q-2", "q-3" })
        {
            (await _store.GetConversationAsync(id))!.ServerId.Should().Be("srv-1");
            (await _store.GetUnsyncedMessagesAsync(id)).Should().BeEmpty();
        }
    }

    [Fact]
    public async Task Offline_Drain_Loses_Nothing_And_Never_ReQueues_Synced_Ids()
    {
        // One id succeeds, the other hits the outage. The drain order over the
        // internal set is unspecified, so assert the invariants that hold
        // either way: the offline id is pending again, and every id is either
        // synced or pending — never silently dropped.
        await SeedLocalConversationAsync("ok-1");
        await SeedLocalConversationAsync("down-1");

        SetupHappyApi();
        _api.Setup(a => a.ImportConversationAsync(
                "down-1", It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));

        _sync.QueueConversationSync("ok-1");
        _sync.QueueConversationSync("down-1");
        await _sync.DrainQueueAsync();

        var pending = _sync.PendingQueueSnapshot;
        pending.Should().Contain("down-1", "the unreachable push must be retried later");

        var okSynced = (await _store.GetConversationAsync("ok-1"))!.ServerId == "srv-1";
        if (okSynced)
            pending.Should().NotContain("ok-1", "a synced id must not be re-queued");
        else
            pending.Should().Contain("ok-1", "an undrained id must never be lost");
    }

    [Fact]
    public async Task Offline_Still_Stops_The_Whole_Sweep()
    {
        await SeedLocalConversationAsync("first-1", updatedAt: DateTimeOffset.UtcNow);
        await SeedLocalConversationAsync("second-1", updatedAt: DateTimeOffset.UtcNow.AddMinutes(-1));

        // No StatusCode = transport failure — nothing was reachable.
        _api.Setup(a => a.ImportConversationAsync(
                "first-1", It.IsAny<string?>(), It.IsAny<string?>(),
                It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));

        await _sync.SyncAllAsync();

        _api.Verify(a => a.ImportConversationAsync(
            "second-1", It.IsAny<string?>(), It.IsAny<string?>(),
            It.IsAny<DateTimeOffset>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    public void Dispose()
    {
        _sync.Dispose();
        SqliteConnection.ClearAllPools();
        try
        {
            if (Directory.Exists(_dbDir)) Directory.Delete(_dbDir, recursive: true);
        }
        catch (IOException)
        {
        }
    }
}
