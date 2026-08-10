using System.Collections.ObjectModel;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 9 — folders merged into projects.
///
/// The rule that must not break: NO local grouping may be lost. A chat the
/// user filed into a folder offline has to come back inside the project
/// that replaced it (chat-edge migration 031 gives the project the folder's
/// id, so the local ids still line up), and a chat that was never filed has
/// to stay unassigned rather than being swept into some project.
/// </summary>
public sealed class ProjectMergeMigrationTests : IDisposable
{
    private readonly string _dbDir;
    private readonly string _dbPath;

    public ProjectMergeMigrationTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-phase9-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_dbDir);
        _dbPath = Path.Combine(_dbDir, "data.db");
    }

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        try { Directory.Delete(_dbDir, recursive: true); } catch { /* temp dir */ }
    }

    /// <summary>Builds a PRE-Phase-9 database: baseline schema through
    /// migration 4, rows carrying folder_id and no project_id column.</summary>
    private async Task SeedLegacyDatabaseAsync(params (string id, string? folderId)[] rows)
    {
        await using var conn = new SqliteConnection(
            new SqliteConnectionStringBuilder { DataSource = _dbPath }.ToString());
        await conn.OpenAsync();

        await using (var cmd = conn.CreateCommand())
        {
            cmd.CommandText = """
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
                -- Pre-Phase-9 database. user_version is the index of the NEXT
                -- migration to run, so 5 means 0..4 are applied and opening
                -- the store runs migration 5 (the folder→project carry-over)
                -- and nothing else.
                PRAGMA user_version = 5;
                """;
            await cmd.ExecuteNonQueryAsync();
        }

        foreach (var (id, folderId) in rows)
        {
            await using var ins = conn.CreateCommand();
            ins.CommandText = """
                INSERT INTO conversations (id, title, model_id, created_at, updated_at, is_pinned, folder_id, source)
                VALUES ($id, $title, 'gpt-4o', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', 0, $folder, 'local');
                """;
            ins.Parameters.AddWithValue("$id", id);
            ins.Parameters.AddWithValue("$title", "Chat " + id);
            ins.Parameters.AddWithValue("$folder", (object?)folderId ?? DBNull.Value);
            await ins.ExecuteNonQueryAsync();
        }
    }

    [Fact]
    public async Task Migration_Carries_Local_Folder_Grouping_Into_Projects()
    {
        await SeedLegacyDatabaseAsync(
            ("c1", "f-work"),
            ("c2", "f-work"),
            ("c3", null));

        var store = new SqliteConversationStore(_dbPath);
        await store.InitializeAsync();
        var conversations = (await store.GetConversationsAsync()).ToList();

        conversations.Should().HaveCount(3, "no chat may be lost by the merge");
        conversations.Single(c => c.Id == "c1").ProjectId.Should().Be("f-work");
        conversations.Single(c => c.Id == "c2").ProjectId.Should().Be("f-work");
        conversations.Single(c => c.Id == "c3").ProjectId.Should()
            .BeNull("an unfiled chat must not be swept into a project");
    }

    [Fact]
    public async Task Migration_Keeps_folder_id_For_Rollback()
    {
        await SeedLegacyDatabaseAsync(("c1", "f-work"));

        var store = new SqliteConversationStore(_dbPath);
        await store.InitializeAsync();

        await using var conn = new SqliteConnection(
            new SqliteConnectionStringBuilder { DataSource = _dbPath }.ToString());
        await conn.OpenAsync();
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT folder_id, project_id FROM conversations WHERE id = 'c1';";
        await using var reader = await cmd.ExecuteReaderAsync();
        (await reader.ReadAsync()).Should().BeTrue();
        reader.GetString(0).Should().Be("f-work", "folder_id is kept for one release so the merge is reversible");
        reader.GetString(1).Should().Be("f-work");
    }

    [Fact]
    public async Task Fresh_Database_Round_Trips_ProjectId()
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
        var conv = (await reopened.GetConversationsAsync()).Single(c => c.Id == "c9");
        conv.ProjectId.Should().Be("p1");
    }
}

/// <summary>
/// The sidebar must render a project that carries NO instructions and NO
/// knowledge base exactly like the folder it replaced — grouping only.
/// </summary>
public sealed class BareProjectSidebarTests
{
    private static ProjectDto Bare(string id, string name) =>
        new(id, name, Instructions: "", KnowledgeBaseId: null,
            ConversationCount: 0,
            CreatedAt: DateTimeOffset.UtcNow, UpdatedAt: DateTimeOffset.UtcNow);

    [Fact]
    public void Bare_Project_Groups_Its_Conversations()
    {
        var conversations = new ObservableCollection<ConversationItemViewModel>();
        var vm = new ProjectItemViewModel(Bare("p1", "Work"), conversations, _ => true);

        conversations.Add(new ConversationItemViewModel(new Conversation
        {
            Id = "c1", Title = "Inside", CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow, ProjectId = "p1",
            Source = ConversationSource.Server,
        }));
        conversations.Add(new ConversationItemViewModel(new Conversation
        {
            Id = "c2", Title = "Outside", CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow, ProjectId = null,
            Source = ConversationSource.Server,
        }));

        vm.Name.Should().Be("Work");
        vm.Conversations.Cast<ConversationItemViewModel>()
            .Select(c => c.Id).Should().Equal("c1");
    }

    [Fact]
    public void Unnamed_Project_Still_Renders_A_Label()
    {
        var vm = new ProjectItemViewModel(
            Bare("p1", ""), new ObservableCollection<ConversationItemViewModel>(), _ => true);
        vm.Name.Should().NotBeNullOrWhiteSpace();
    }
}
