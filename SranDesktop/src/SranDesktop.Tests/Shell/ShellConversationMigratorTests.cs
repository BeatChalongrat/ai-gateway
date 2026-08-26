using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Shell.Hosting;
using Xunit;

namespace SranDesktop.Tests.Shell;

/// <summary>
/// Phase 5 §4.1 / criterion 6 — the local conversation migration, on a REAL
/// SQLite store with REAL rows. Classification is by (Source + ServerId), NOT by
/// ServerId alone — the bug the first live run exposed: every server-created
/// conversation keeps its local server_id NULL by design, so a ServerId-only
/// test counts them all as local-only. These tests pin the corrected rule.
/// </summary>
public sealed class ShellConversationMigratorTests : IDisposable
{
    private readonly string _dbPath = Path.Combine(Path.GetTempPath(), $"migrate-{Guid.NewGuid():N}.db");
    private readonly string _exportDir = Path.Combine(Path.GetTempPath(), $"migrate-export-{Guid.NewGuid():N}");

    private static async Task Add(
        IConversationStore store, string id, ConversationSource source, string? serverId, int messages)
    {
        var now = DateTimeOffset.UtcNow;
        await store.UpsertConversationAsync(new Conversation
        {
            Id = id, Title = id, CreatedAt = now, UpdatedAt = now, Source = source, ServerId = serverId,
        });
        for (var i = 0; i < messages; i++)
            await store.UpsertMessageAsync(new Message
            {
                Id = $"{id}-m{i}", ConversationId = id,
                Role = i % 2 == 0 ? MessageRole.User : MessageRole.Assistant,
                Content = $"message {i} of {id}", CreatedAt = now, SortOrder = i,
            });
    }

    private async Task<SqliteConversationStore> NewStoreAsync()
    {
        var store = new SqliteConversationStore(_dbPath);
        await store.InitializeAsync();
        return store;
    }

    private ShellConversationMigrator Migrator(SqliteConversationStore store, string? markServerIdFor) =>
        new(store, new FakeSync(store, markServerIdFor), NullLogger<ShellConversationMigrator>.Instance);

    [Fact]
    public async Task Server_Source_Conversations_Are_Already_On_The_Server_Even_With_Null_ServerId()
    {
        // This is the exact shape of the real store that produced the 0/32 bug:
        // every conversation is Source=Server with server_id NULL. Nothing is
        // local-only, nothing is exported, and no push is even attempted.
        var store = await NewStoreAsync();
        for (var i = 0; i < 5; i++)
            await Add(store, $"srv-{i}", ConversationSource.Server, serverId: null, messages: 2);

        var result = await Migrator(store, markServerIdFor: null).MigrateAsync(_exportDir);

        result.Total.Should().Be(5);
        result.OnServer.Should().Be(5, "server-source conversations are already on the server");
        result.MigratedUp.Should().Be(0);
        result.LocalOnlyRemaining.Should().Be(0);
        result.ExportPath.Should().BeNull();
        File.Exists(Path.Combine(_exportDir, "local-conversations-export.json")).Should().BeFalse();
    }

    [Fact]
    public async Task A_Local_Only_Conversation_Is_Migrated_Up_When_The_Push_Succeeds()
    {
        var store = await NewStoreAsync();
        await Add(store, "srv-a", ConversationSource.Server, serverId: null, messages: 2);   // already on server
        await Add(store, "local-mirrored", ConversationSource.Local, serverId: "srv-x", messages: 2); // mirrored
        await Add(store, "local-only", ConversationSource.Local, serverId: null, messages: 3); // AT RISK

        var before = await store.GetConversationsAsync();
        before.Count(c => c.Source == ConversationSource.Local && string.IsNullOrEmpty(c.ServerId))
            .Should().Be(1, "one genuinely local-only conversation before migration");

        // A push that reaches the server stamps the local-only row with a ServerId.
        var result = await Migrator(store, markServerIdFor: "local-only").MigrateAsync(_exportDir);

        result.Total.Should().Be(3);
        result.OnServer.Should().Be(3, "all three are on the server after the push");
        result.MigratedUp.Should().Be(1, "the one local-only conversation gained a server home");
        result.LocalOnlyRemaining.Should().Be(0);
        result.ExportPath.Should().BeNull();
    }

    [Fact]
    public async Task An_Unreachable_Server_Leaves_The_Local_Only_Row_And_Exports_It_With_Its_Messages()
    {
        var store = await NewStoreAsync();
        await Add(store, "srv-a", ConversationSource.Server, serverId: null, messages: 2);
        await Add(store, "local-only", ConversationSource.Local, serverId: null, messages: 4); // AT RISK

        // A push that cannot reach the server changes nothing — the local-only
        // row stays local-only and must be exported so it is not lost silently.
        var result = await Migrator(store, markServerIdFor: null).MigrateAsync(_exportDir);

        result.Total.Should().Be(2);
        result.OnServer.Should().Be(1, "only the server-source conversation is on the server");
        result.MigratedUp.Should().Be(0);
        result.LocalOnlyRemaining.Should().Be(1);
        result.ExportPath.Should().NotBeNull();
        File.Exists(result.ExportPath!).Should().BeTrue();

        using var doc = JsonDocument.Parse(await File.ReadAllTextAsync(result.ExportPath!));
        doc.RootElement.GetProperty("exportedConversations").GetInt32().Should().Be(1);
        var exported = doc.RootElement.GetProperty("conversations")[0];
        exported.GetProperty("id").GetString().Should().Be("local-only");
        exported.GetProperty("messages").GetArrayLength().Should().Be(4, "all four messages are preserved");
    }

    /// <summary>Stands in for the real push-sync: optionally stamps a ServerId on
    /// one conversation (a successful push of a local-only row) or does nothing.</summary>
    private sealed class FakeSync : IAgentConversationSyncService
    {
        private readonly IConversationStore _store;
        private readonly string? _markServerIdFor;
        public FakeSync(IConversationStore store, string? markServerIdFor)
        {
            _store = store;
            _markServerIdFor = markServerIdFor;
        }
        public event EventHandler<string>? RemoteConversationDeleted;
        public async Task SyncAllAsync(CancellationToken ct = default)
        {
            _ = RemoteConversationDeleted;
            if (_markServerIdFor is not null)
                await _store.SetConversationServerIdAsync(_markServerIdFor, "srv-migrated", ct);
        }
        public void QueueConversationSync(string conversationId) { }
        public Task<bool> SyncConversationNowAsync(string conversationId, CancellationToken ct = default) => Task.FromResult(true);
    }

    public void Dispose()
    {
        try { if (File.Exists(_dbPath)) File.Delete(_dbPath); } catch { /* temp */ }
        try { if (Directory.Exists(_exportDir)) Directory.Delete(_exportDir, recursive: true); } catch { /* temp */ }
    }
}
