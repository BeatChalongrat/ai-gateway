using FluentAssertions;
using Microsoft.Data.Sqlite;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Storage;

/// <summary>Phase 3: transactional turn persistence (C3), drafts (C4) and
/// push-sync state (D) on SqliteConversationStore.</summary>
public class SqliteStorePhase3Tests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;

    public SqliteStorePhase3Tests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-db-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
    }

    private static Conversation LocalConversation(string id = "c1") => new()
    {
        Id = id,
        Title = "Agent chat",
        ModelId = "gpt-4o-mini",
        CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-10),
        UpdatedAt = DateTimeOffset.UtcNow,
        Source = ConversationSource.Local
    };

    private static Message Msg(string id, string convId, MessageRole role, string content, int sort) => new()
    {
        Id = id, ConversationId = convId, Role = role, Content = content,
        CreatedAt = DateTimeOffset.UtcNow.AddSeconds(sort), SortOrder = sort
    };

    // ── C3: transactional batch upsert ──────────────────────────────────────

    [Fact]
    public async Task UpsertMessages_Persists_All_Rows()
    {
        await _store.UpsertConversationAsync(LocalConversation());

        await _store.UpsertMessagesAsync(new[]
        {
            Msg("u1", "c1", MessageRole.User, "hello", 0),
            Msg("a1", "c1", MessageRole.Assistant, "hi there", 1),
        });

        (await _store.GetMessagesAsync("c1")).Should().HaveCount(2);
    }

    [Fact]
    public async Task UpsertMessages_Is_Atomic_On_Failure()
    {
        await _store.UpsertConversationAsync(LocalConversation());

        // Second row violates the messages→conversations FK; the first row
        // must roll back with it.
        var act = () => _store.UpsertMessagesAsync(new[]
        {
            Msg("u1", "c1", MessageRole.User, "hello", 0),
            Msg("a1", "missing-conversation", MessageRole.Assistant, "hi", 1),
        });

        await act.Should().ThrowAsync<SqliteException>();
        (await _store.GetMessagesAsync("c1")).Should().BeEmpty();
    }

    // ── C4: drafts ──────────────────────────────────────────────────────────

    [Fact]
    public async Task Draft_RoundTrips()
    {
        await _store.SaveDraftAsync("c1", "unsent thought…");

        (await _store.GetDraftAsync("c1")).Should().Be("unsent thought…");
    }

    [Fact]
    public async Task Draft_Save_Overwrites()
    {
        await _store.SaveDraftAsync("c1", "first");
        await _store.SaveDraftAsync("c1", "second");

        (await _store.GetDraftAsync("c1")).Should().Be("second");
    }

    [Fact]
    public async Task Empty_Draft_Deletes_The_Row()
    {
        await _store.SaveDraftAsync("c1", "something");
        await _store.SaveDraftAsync("c1", "");

        (await _store.GetDraftAsync("c1")).Should().BeNull();
    }

    // ── D: push-sync state ──────────────────────────────────────────────────

    [Fact]
    public async Task ServerId_Set_And_Survives_Reconciliation_Upsert()
    {
        var conversation = LocalConversation();
        await _store.UpsertConversationAsync(conversation);
        await _store.SetConversationServerIdAsync("c1", "srv-1");

        // A reconciliation-style upsert carries no ServerId — the mapping
        // (and thus sync state) must survive it.
        conversation.Title = "Renamed";
        await _store.UpsertConversationAsync(conversation);

        var loaded = await _store.GetConversationAsync("c1");
        loaded!.ServerId.Should().Be("srv-1");
        loaded.LastSyncedAt.Should().NotBeNull();
        loaded.Title.Should().Be("Renamed");
    }

    [Fact]
    public async Task Unsynced_Messages_Are_Marked_And_Excluded_After_Sync()
    {
        await _store.UpsertConversationAsync(LocalConversation());
        await _store.UpsertMessagesAsync(new[]
        {
            Msg("u1", "c1", MessageRole.User, "hello", 0),
            Msg("a1", "c1", MessageRole.Assistant, "hi", 1),
        });

        var unsynced = await _store.GetUnsyncedMessagesAsync("c1");
        unsynced.Select(m => m.Id).Should().Equal("u1", "a1"); // chronological

        await _store.MarkMessagesSyncedAsync(new[] { "u1", "a1" }, "srv-1");

        (await _store.GetUnsyncedMessagesAsync("c1")).Should().BeEmpty();
        (await _store.GetMessagesAsync("c1")).Should().OnlyContain(m => m.ServerId == "srv-1");
    }

    [Fact]
    public async Task Content_ReUpsert_Keeps_Synced_Marker()
    {
        await _store.UpsertConversationAsync(LocalConversation());
        var message = Msg("u1", "c1", MessageRole.User, "hello", 0);
        await _store.UpsertMessageAsync(message);
        await _store.MarkMessagesSyncedAsync(new[] { "u1" }, "srv-1");

        // Re-upsert with no ServerId (e.g. a content refresh) — the synced
        // marker must not be wiped, or the row would re-import forever.
        await _store.UpsertMessageAsync(Msg("u1", "c1", MessageRole.User, "hello edited", 0));

        (await _store.GetUnsyncedMessagesAsync("c1")).Should().BeEmpty();
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_dbDir)) Directory.Delete(_dbDir, recursive: true);
        }
        catch (IOException)
        {
        }
    }
}
