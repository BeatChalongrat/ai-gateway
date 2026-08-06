using System.Net.Http;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Hotfix regression tests for "Failed to load conversations" (Phase 9).
///
/// ROOT CAUSE this pins:
/// PRAGMA user_version is a single counter shared by every build that ever
/// opened the file. A user who ran a build from the abandoned 22-30 Jul
/// branch left data.db at user_version 10; main was rolled back to a list
/// of 6. SqliteMigrator's loop is `for (i = version; i &lt; Count; i++)`, so
/// with 10 &gt;= 6 it runs NOTHING — the Phase-9 `project_id` migration was
/// skipped silently, and the SELECT that reads that column died with
/// "no such column: project_id", taking the whole sidebar with it.
///
/// Two independent guarantees are asserted here:
///   1. the store repairs its own schema regardless of the version counter;
///   2. LoadAsync shows the chat history even when the steps around it fail.
/// </summary>
public sealed class ConversationSchemaSelfRepairTests : IDisposable
{
    private readonly string _dbDir;
    private readonly string _dbPath;

    public ConversationSchemaSelfRepairTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-hotfix-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_dbDir);
        _dbPath = Path.Combine(_dbDir, "data.db");
    }

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        try { Directory.Delete(_dbDir, recursive: true); } catch { /* temp dir */ }
    }

    /// <summary>Reproduces the user's database EXACTLY: pre-Phase-9 columns,
    /// but a user_version far ahead of this build's migration list.</summary>
    private async Task SeedDatabaseFromAnotherLineageAsync(int userVersion, params (string id, string? folderId)[] rows)
    {
        await using var conn = new SqliteConnection(
            new SqliteConnectionStringBuilder { DataSource = _dbPath }.ToString());
        await conn.OpenAsync();

        await using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = $"""
                CREATE TABLE conversations (
                    id              TEXT PRIMARY KEY,
                    title           TEXT,
                    model_id        TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    is_pinned       INTEGER DEFAULT 0,
                    folder_id       TEXT,
                    source          TEXT NOT NULL DEFAULT 'server',
                    last_synced_at  TEXT,
                    server_id       TEXT
                );
                PRAGMA user_version = {userVersion};
                """;
            await cmd.ExecuteNonQueryAsync();
        }

        foreach (var (id, folderId) in rows)
        {
            await using var ins = conn.CreateCommand();
            ins.CommandText = """
                INSERT INTO conversations (id, title, model_id, created_at, updated_at, is_pinned, folder_id, source)
                VALUES ($id, $title, 'gpt-4o', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', 0, $folder, 'server');
                """;
            ins.Parameters.AddWithValue("$id", id);
            ins.Parameters.AddWithValue("$title", "Chat " + id);
            ins.Parameters.AddWithValue("$folder", (object?)folderId ?? DBNull.Value);
            await ins.ExecuteNonQueryAsync();
        }
    }

    [Fact]
    public async Task Reads_Conversations_When_user_version_Is_Ahead_Of_The_Migration_List()
    {
        // user_version 10 = the abandoned-branch build. The migrator will do
        // nothing; only the schema self-repair can save this.
        await SeedDatabaseFromAnotherLineageAsync(10, ("c1", "f-work"), ("c2", null));

        var store = new SqliteConversationStore(_dbPath);
        var conversations = await store.GetConversationsAsync();

        conversations.Should().HaveCount(2,
            "the sidebar must load even when the database came from another build lineage");
    }

    [Fact]
    public async Task Repair_Still_Carries_The_Local_Grouping_Across()
    {
        await SeedDatabaseFromAnotherLineageAsync(10, ("c1", "f-work"), ("c2", null));

        var store = new SqliteConversationStore(_dbPath);
        var conversations = (await store.GetConversationsAsync()).ToList();

        conversations.Single(c => c.Id == "c1").ProjectId.Should().Be("f-work",
            "a repaired column must be back-filled like migration 5 would have");
        conversations.Single(c => c.Id == "c2").ProjectId.Should().BeNull();
    }

    [Fact]
    public async Task Repair_Is_Idempotent_Across_Reopens()
    {
        await SeedDatabaseFromAnotherLineageAsync(10, ("c1", "f-work"));

        var first = new SqliteConversationStore(_dbPath);
        (await first.GetConversationsAsync()).Should().HaveCount(1);

        // Second open must not try to ADD COLUMN again (SQLite has no
        // IF NOT EXISTS — a blind retry would throw "duplicate column").
        var second = new SqliteConversationStore(_dbPath);
        (await second.GetConversationsAsync()).Should().HaveCount(1);
    }

    [Fact]
    public async Task Normal_Path_Still_Works_When_The_Version_Is_Correct()
    {
        // user_version 5 = a database that legitimately stopped before the
        // Phase-9 migration; the migrator applies it and the repair is a
        // no-op. Both routes must reach the same place.
        await SeedDatabaseFromAnotherLineageAsync(5, ("c1", "f-work"));

        var store = new SqliteConversationStore(_dbPath);
        var conv = (await store.GetConversationsAsync()).Single();
        conv.ProjectId.Should().Be("f-work");
    }

    [Fact]
    public async Task Fresh_Database_Is_Unaffected()
    {
        var store = new SqliteConversationStore(_dbPath);
        await store.InitializeAsync();
        await store.UpsertConversationAsync(new Conversation
        {
            Id = "c9",
            Title = "New",
            ModelId = "gpt-4o",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            ProjectId = "p1",
            Source = ConversationSource.Local,
        });

        var reopened = new SqliteConversationStore(_dbPath);
        (await reopened.GetConversationsAsync()).Single().ProjectId.Should().Be("p1");
    }
}

/// <summary>
/// LoadAsync must keep the chat history on screen when a step AROUND it
/// fails. Before the hotfix a single try/catch wrapped the whole method, so
/// any failure — including one raised after the list had been read — left
/// the user with "Failed to load conversations" and an empty sidebar.
/// </summary>
public sealed class ConversationLoadIsolationTests
{
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<IConversationStore> _store = new();
    private readonly Mock<IConversationSyncService> _sync = new();
    private readonly Mock<IAgentConversationSyncService> _agentSync = new();
    private readonly Mock<ISettingsService> _settings = new();

    private static Conversation Entity(string id) => new()
    {
        Id = id,
        Title = "Chat " + id,
        ModelId = "gpt-4o",
        CreatedAt = DateTimeOffset.UtcNow,
        UpdatedAt = DateTimeOffset.UtcNow,
        Source = ConversationSource.Server,
    };

    private ConversationListViewModel Create()
    {
        _settings.SetupGet(s => s.Current).Returns(new UserSettings());
        _store.Setup(s => s.GetConversationsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new[] { Entity("c1"), Entity("c2") });
        _sync.Setup(s => s.SyncFromServerAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(true);
        _api.Setup(a => a.ListModelsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ModelListResponse(new List<ModelInfo>()));
        _api.Setup(a => a.ListProjectsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ProjectListResponse(new List<ProjectDto>()));
        return new ConversationListViewModel(
            _api.Object, _store.Object, _sync.Object, _agentSync.Object,
            _settings.Object, Mock.Of<IDialogService>(),
            NullLogger<ConversationListViewModel>.Instance);
    }

    [Fact]
    public async Task Chat_History_Loads_When_The_Projects_Fetch_Throws()
    {
        var vm = Create();
        _api.Setup(a => a.ListProjectsAsync(It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("boom"));

        await vm.LoadAsync();

        vm.Conversations.Should().HaveCount(2,
            "a failing projects call must never blank the chat history");
        vm.StatusMessage.Should().BeNull("the history loaded fine — do not cry failure");
    }

    [Fact]
    public async Task Chat_History_Loads_When_Projects_Returns_A_Non_Http_Error()
    {
        // The pre-hotfix catch filter only handled HttpRequestException /
        // TaskCanceledException, so anything else (a JSON shape change, a
        // 404 surfacing as something else) escaped and killed the load.
        var vm = Create();
        _api.Setup(a => a.ListProjectsAsync(It.IsAny<CancellationToken>()))
            .ThrowsAsync(new InvalidOperationException("unexpected payload"));

        await vm.LoadAsync();

        vm.Conversations.Should().HaveCount(2);
    }

    [Fact]
    public async Task Chat_History_Loads_When_The_Model_Catalog_Fails()
    {
        var vm = Create();
        _api.Setup(a => a.ListModelsAsync(It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("boom"));

        await vm.LoadAsync();

        vm.Conversations.Should().HaveCount(2);
    }

    [Fact]
    public async Task Local_History_Survives_A_Failing_Server_Sync()
    {
        var vm = Create();
        _sync.Setup(s => s.SyncFromServerAsync(It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));

        await vm.LoadAsync();

        vm.Conversations.Should().HaveCount(2, "the local mirror is still readable");
        vm.StatusMessage.Should().Be("Offline - showing local history",
            "an unreachable server is offline, not a hard failure");
    }

    [Fact]
    public async Task Reports_A_Hard_Failure_Only_When_Nothing_Could_Be_Shown()
    {
        // Both the local store AND the server are broken: this is the one
        // case the user should see the error for.
        var vm = Create();
        _store.Setup(s => s.GetConversationsAsync(It.IsAny<CancellationToken>()))
            .ThrowsAsync(new InvalidOperationException("no such column: project_id"));
        _sync.Setup(s => s.SyncFromServerAsync(It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));

        await vm.LoadAsync();

        vm.Conversations.Should().BeEmpty();
        vm.StatusMessage.Should().Be("Failed to load conversations");
    }

    [Fact]
    public async Task A_Broken_Local_Store_Still_Recovers_From_The_Server()
    {
        // The exact user scenario, from the other side: the local read blew
        // up, but the server sync repaired the store and the second read
        // succeeded. The sidebar must end up populated, not empty.
        var vm = Create();
        var calls = 0;
        _store.Setup(s => s.GetConversationsAsync(It.IsAny<CancellationToken>()))
            .Returns(() =>
            {
                calls++;
                return calls == 1
                    ? throw new InvalidOperationException("no such column: project_id")
                    : Task.FromResult<IReadOnlyList<Conversation>>(new[] { Entity("c1") });
            });

        await vm.LoadAsync();

        vm.Conversations.Should().HaveCount(1);
        vm.StatusMessage.Should().BeNull();
    }
}
