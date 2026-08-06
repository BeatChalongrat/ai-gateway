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
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 14 Task 1 — archiving conversations on the desktop (web P10 parity).
///
/// The load-bearing distinction is ARCHIVE ≠ DELETE. Everything here exists to
/// stop the two collapsing into one: the chat leaves the sidebar but survives
/// in the store, stays openable, and comes back on restore.
/// </summary>
public sealed class ArchiveConversationTests
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
            _settings.Object, Mock.Of<IDialogService>(),
            NullLogger<ConversationListViewModel>.Instance);
    }

    private static ConversationItemViewModel Item(
        string id = "c1", bool archived = false,
        ConversationSource source = ConversationSource.Server) =>
        new(new Conversation
        {
            Id = id,
            Title = "Chat " + id,
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            IsArchived = archived,
            Source = source,
        });

    [Fact]
    public async Task Archiving_Writes_The_Server_Flag_And_The_Local_Mirror()
    {
        var vm = Create();
        var item = Item();
        vm.Conversations.Add(item);

        await vm.ToggleArchiveCommand.ExecuteAsync(item);

        _api.Verify(a => a.SetConversationArchivedAsync("c1", true, It.IsAny<CancellationToken>()), Times.Once);
        _store.Verify(s => s.UpsertConversationAsync(
            It.Is<Conversation>(c => c.Id == "c1" && c.IsArchived),
            It.IsAny<CancellationToken>()), Times.Once);
        item.IsArchived.Should().BeTrue();
    }

    [Fact]
    public async Task Restoring_Clears_The_Flag_On_Both_Sides()
    {
        var vm = Create();
        var item = Item(archived: true);
        vm.Conversations.Add(item);

        await vm.ToggleArchiveCommand.ExecuteAsync(item);

        _api.Verify(a => a.SetConversationArchivedAsync("c1", false, It.IsAny<CancellationToken>()), Times.Once);
        item.IsArchived.Should().BeFalse();
    }

    [Fact]
    public async Task The_Chat_Is_Never_Deleted()
    {
        var vm = Create();
        var item = Item();
        vm.Conversations.Add(item);

        await vm.ToggleArchiveCommand.ExecuteAsync(item);

        _store.Verify(s => s.DeleteConversationAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        _api.Verify(a => a.DeleteConversationAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        vm.Conversations.Should().Contain(item, "it moves lists, it does not disappear");
    }

    [Fact]
    public async Task A_Rejected_Archive_Leaves_Everything_Untouched()
    {
        // Server-first on purpose: this decides which LIST the chat is in, so
        // a local-only flag would hide it here and leave it visible on the web.
        _api.Setup(a => a.SetConversationArchivedAsync(
                It.IsAny<string>(), It.IsAny<bool>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));
        var vm = Create();
        var item = Item();
        vm.Conversations.Add(item);

        await vm.ToggleArchiveCommand.ExecuteAsync(item);

        item.IsArchived.Should().BeFalse();
        _store.Verify(s => s.UpsertConversationAsync(
            It.IsAny<Conversation>(), It.IsAny<CancellationToken>()), Times.Never);
        vm.StatusMessage.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task Archiving_The_Open_Chat_Clears_The_Selection()
    {
        // Mirrors web: the chat pane must not keep showing a chat that is no
        // longer in the list the user is looking at.
        var vm = Create();
        var item = Item();
        vm.Conversations.Add(item);
        vm.SelectedConversation = item;
        ConversationItemViewModel? lastSelected = item;
        vm.ConversationSelected += (_, c) => lastSelected = c;

        await vm.ToggleArchiveCommand.ExecuteAsync(item);

        vm.SelectedConversation.Should().BeNull();
        lastSelected.Should().BeNull("the chat view is told to go back to home");
    }

    [Fact]
    public async Task A_Local_Agent_Chat_Archives_Through_Its_Server_Mirror()
    {
        _store.Setup(s => s.GetConversationAsync("local-1", It.IsAny<CancellationToken>()))
            .ReturnsAsync(new Conversation
            {
                Id = "local-1",
                CreatedAt = DateTimeOffset.UtcNow,
                UpdatedAt = DateTimeOffset.UtcNow,
                Source = ConversationSource.Local,
                ServerId = "mirror-9",
            });
        var vm = Create();
        var item = Item("local-1", source: ConversationSource.Local);
        vm.Conversations.Add(item);

        await vm.ToggleArchiveCommand.ExecuteAsync(item);

        _api.Verify(a => a.SetConversationArchivedAsync("mirror-9", true, It.IsAny<CancellationToken>()), Times.Once);
        item.IsArchived.Should().BeTrue();
    }

    [Fact]
    public async Task An_Unsynced_Local_Chat_Archives_Locally_Without_A_Server_Call()
    {
        _store.Setup(s => s.GetConversationAsync("local-2", It.IsAny<CancellationToken>()))
            .ReturnsAsync(new Conversation
            {
                Id = "local-2",
                CreatedAt = DateTimeOffset.UtcNow,
                UpdatedAt = DateTimeOffset.UtcNow,
                Source = ConversationSource.Local,
            });
        var vm = Create();
        var item = Item("local-2", source: ConversationSource.Local);
        vm.Conversations.Add(item);

        await vm.ToggleArchiveCommand.ExecuteAsync(item);

        _api.Verify(a => a.SetConversationArchivedAsync(
            It.IsAny<string>(), It.IsAny<bool>(), It.IsAny<CancellationToken>()), Times.Never);
        item.IsArchived.Should().BeTrue();
    }

    [Fact]
    public void Wrong_Type_Parameters_Do_Not_Throw()
    {
        // FIX 12: the command is reachable from a heterogeneous ItemsControl.
        var vm = Create();
        vm.ToggleArchiveCommand.CanExecute("a string").Should().BeTrue();
        vm.ToggleArchiveCommand.CanExecute(null).Should().BeTrue();
    }

    // ── The two views ──────────────────────────────────────────────────────

    [Fact]
    public void The_Main_List_Hides_Archived_Chats_And_The_Archive_Shows_Only_Those()
    {
        var vm = Create();
        vm.Conversations.Add(Item("live"));
        vm.Conversations.Add(Item("filed", archived: true));

        vm.RebuildSidebarRowsNow();
        vm.SidebarRows.OfType<ConversationItemViewModel>().Select(c => c.Id)
            .Should().Equal("live");

        vm.IsArchiveView = true;
        vm.SidebarRows.OfType<ConversationItemViewModel>().Select(c => c.Id)
            .Should().Equal("filed");
    }

    [Fact]
    public void Entering_The_Archive_Flips_The_Main_View_Flag_For_The_Chrome()
    {
        var vm = Create();
        vm.IsMainView.Should().BeTrue();

        vm.ShowArchiveCommand.Execute(null);
        vm.IsArchiveView.Should().BeTrue();
        vm.IsMainView.Should().BeFalse("New chat / search hide in the archive");

        vm.ExitArchiveCommand.Execute(null);
        vm.IsArchiveView.Should().BeFalse();
    }

    [Fact]
    public void An_Empty_Archive_Says_So_Instead_Of_Showing_A_Blank_Panel()
    {
        var vm = Create();
        vm.ShowArchiveEmptyState.Should().BeFalse("not in the archive view");

        vm.IsArchiveView = true;
        vm.ShowArchiveEmptyState.Should().BeTrue();

        vm.IsLoading = true;
        vm.ShowArchiveEmptyState.Should().BeFalse("the skeleton owns the loading state");
    }

    [Fact]
    public void The_Archive_View_Is_Flat_Even_When_Projects_Exist()
    {
        var vm = Create();
        vm.Conversations.Add(Item("filed", archived: true));
        vm.Conversations[0].ProjectId = "p1";
        vm.Projects.Add(new ProjectItemViewModel(
            new ProjectDto("p1", "Work", "", null, 0, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow),
            vm.Conversations,
            _ => true));

        vm.IsArchiveView = true;

        vm.SidebarRows.OfType<ProjectItemViewModel>().Should().BeEmpty(
            "the archive is a recovery surface, not a filing view");
        vm.SidebarRows.OfType<ConversationItemViewModel>().Select(c => c.Id)
            .Should().Equal("filed");
    }

    [Fact]
    public void Archive_Strings_Exist_In_Both_Languages()
    {
        foreach (var key in new[]
                 {
                     "conv.archive", "conv.unarchive", "sidebar.archive",
                     "sidebar.archiveTitle", "sidebar.backToChats", "sidebar.archiveEmpty",
                 })
        {
            SranDesktop.Presentation.Localization.Dict.En.Should().ContainKey(key);
            SranDesktop.Presentation.Localization.Dict.Th.Should().ContainKey(key);
        }
    }
}

/// <summary>
/// Phase 14 Task 1 — the local column, against a REAL SQLite file.
///
/// HARD RULE from the Phase-9 hotfix: a new column goes in the migration list
/// AND in RequiredColumns, because PRAGMA user_version is shared across build
/// lineages and can silently skip every migration. The second test here is
/// that rule, enforced.
/// </summary>
public sealed class ArchiveColumnTests : IDisposable
{
    private readonly string _dir;
    private readonly string _dbPath;

    public ArchiveColumnTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), $"sran-archive-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_dir);
        _dbPath = Path.Combine(_dir, "data.db");
    }

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        try { Directory.Delete(_dir, recursive: true); } catch { /* temp dir */ }
    }

    private static Conversation Conv(string id, bool archived) => new()
    {
        Id = id,
        Title = id,
        CreatedAt = DateTimeOffset.UtcNow,
        UpdatedAt = DateTimeOffset.UtcNow,
        IsArchived = archived,
    };

    [Fact]
    public async Task The_Flag_Round_Trips_Through_The_Store()
    {
        var store = new SqliteConversationStore(_dbPath);
        await store.UpsertConversationAsync(Conv("a", archived: false));
        await store.UpsertConversationAsync(Conv("b", archived: true));

        var all = await store.GetConversationsAsync();

        all.Single(c => c.Id == "a").IsArchived.Should().BeFalse();
        all.Single(c => c.Id == "b").IsArchived.Should().BeTrue();
        (await store.GetConversationAsync("b"))!.IsArchived.Should().BeTrue();
    }

    [Fact]
    public async Task Archiving_Then_Restoring_Keeps_The_Conversation()
    {
        var store = new SqliteConversationStore(_dbPath);
        await store.UpsertConversationAsync(Conv("a", archived: false));

        var entity = (await store.GetConversationAsync("a"))!;
        entity.IsArchived = true;
        await store.UpsertConversationAsync(entity);
        (await store.GetConversationAsync("a"))!.IsArchived.Should().BeTrue();

        entity.IsArchived = false;
        await store.UpsertConversationAsync(entity);
        var restored = await store.GetConversationAsync("a");
        restored.Should().NotBeNull();
        restored!.IsArchived.Should().BeFalse();
    }

    [Fact]
    public async Task The_Column_Is_Repaired_When_The_Version_Counter_Skips_The_Migration()
    {
        // Reproduces the Phase-9 trap: a database left at a user_version far
        // ahead of this build's migration list runs NO migrations at all.
        await using (var conn = new SqliteConnection(
            new SqliteConnectionStringBuilder { DataSource = _dbPath }.ToString()))
        {
            await conn.OpenAsync();
            await using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                CREATE TABLE conversations (
                    id              TEXT PRIMARY KEY,
                    title           TEXT,
                    model_id        TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    is_pinned       INTEGER DEFAULT 0,
                    folder_id       TEXT,
                    project_id      TEXT,
                    source          TEXT NOT NULL DEFAULT 'server',
                    last_synced_at  TEXT,
                    server_id       TEXT
                );
                INSERT INTO conversations (id, title, created_at, updated_at, source)
                VALUES ('kept', 'Old chat', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'server');
                PRAGMA user_version = 99;
                """;
            await cmd.ExecuteNonQueryAsync();
        }

        var store = new SqliteConversationStore(_dbPath);
        var all = await store.GetConversationsAsync();

        all.Should().ContainSingle().Which.Id.Should().Be("kept");
        all[0].IsArchived.Should().BeFalse("a repaired column defaults to not-archived");
    }
}

/// <summary>
/// Phase 14 Task 1 — the sync must fetch the archive too.
///
/// This is the sharp edge: the reconcile deletes any local server-sourced row
/// the sync did not see, so fetching only the ACTIVE list would delete the
/// local copy of every chat the moment it was archived.
/// </summary>
public sealed class ArchiveSyncTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<IConversationStore> _store = new();

    private static ConversationDto Dto(string id, bool archived = false) =>
        new(id, "Chat " + id, "gpt-4o-mini", 1,
            DateTimeOffset.UtcNow, DateTimeOffset.UtcNow, false, null,
            IsArchived: archived);

    private static ConversationListResponse One(params ConversationDto[] items) =>
        new(items.ToList(), false, null);

    private ConversationSyncService Create()
    {
        _store.Setup(s => s.GetConversationsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new List<Conversation>());
        return new ConversationSyncService(
            _api.Object, _store.Object, NullLogger<ConversationSyncService>.Instance);
    }

    [Fact]
    public async Task Fetches_Both_Lists_And_Keeps_The_Archived_Rows()
    {
        _api.Setup(a => a.ListConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(One(Dto("live")));
        _api.Setup(a => a.ListArchivedConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(One(Dto("filed", archived: true)));
        var svc = Create();

        (await svc.SyncFromServerAsync()).Should().BeTrue();

        _store.Verify(s => s.UpsertConversationAsync(
            It.Is<Conversation>(c => c.Id == "live" && !c.IsArchived), It.IsAny<CancellationToken>()), Times.Once);
        _store.Verify(s => s.UpsertConversationAsync(
            It.Is<Conversation>(c => c.Id == "filed" && c.IsArchived), It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task An_Archived_Chat_Is_Not_Swept_Away_As_A_Server_Side_Delete()
    {
        _api.Setup(a => a.ListConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(One());
        _api.Setup(a => a.ListArchivedConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(One(Dto("filed", archived: true)));
        _store.Setup(s => s.GetConversationsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new List<Conversation>
            {
                new()
                {
                    Id = "filed", CreatedAt = DateTimeOffset.UtcNow, UpdatedAt = DateTimeOffset.UtcNow,
                    Source = ConversationSource.Server, IsArchived = true,
                },
            });
        var svc = new ConversationSyncService(
            _api.Object, _store.Object, NullLogger<ConversationSyncService>.Instance);

        await svc.SyncFromServerAsync();

        _store.Verify(s => s.DeleteConversationAsync("filed", It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task A_Failed_Archive_Fetch_Still_Syncs_Live_Chats_But_Skips_The_Sweep()
    {
        // The archive list is best-effort (an older server may not have the
        // route). Losing it must not stop live chats from syncing — but it
        // MUST stop the deletion sweep, or every archived chat would be
        // deleted locally for having "disappeared" from the server.
        _api.Setup(a => a.ListConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(One(Dto("live")));
        _api.Setup(a => a.ListArchivedConversationsAsync(null, It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("no such route"));
        _store.Setup(s => s.GetConversationsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new List<Conversation>
            {
                new()
                {
                    Id = "filed", CreatedAt = DateTimeOffset.UtcNow, UpdatedAt = DateTimeOffset.UtcNow,
                    Source = ConversationSource.Server, IsArchived = true,
                },
            });
        var svc = new ConversationSyncService(
            _api.Object, _store.Object, NullLogger<ConversationSyncService>.Instance);

        (await svc.SyncFromServerAsync()).Should().BeTrue("the live list came through");

        _store.Verify(s => s.UpsertConversationAsync(
            It.Is<Conversation>(c => c.Id == "live"), It.IsAny<CancellationToken>()), Times.Once);
        _store.Verify(s => s.DeleteConversationAsync(
            It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }
}
