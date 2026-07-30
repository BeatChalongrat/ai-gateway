using System.Text.Json;
using FluentAssertions;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Mcp.Tools;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Phase B PART 3: cross-session agent memory — the remember tool, the
/// store's note persistence (scope + cap), and the ## Notes formatter.</summary>
public class AgentMemoryTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;

    public AgentMemoryTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-notes-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
    }

    private static JsonElement Args(object o) => JsonSerializer.SerializeToElement(o);

    private static AgentNote Note(string content, int order) => new()
    {
        Id = $"n{order}", ConversationId = "c1", Content = content,
        CreatedAt = DateTimeOffset.UtcNow.AddSeconds(order)
    };

    // ── store ───────────────────────────────────────────────────────────────

    [Fact]
    public async Task Notes_Persist_And_Reload_Across_A_Store_Roundtrip()
    {
        await _store.AddNoteAsync("c1", "builds with dotnet build src/X.sln");
        await _store.AddNoteAsync("c1", "config lives in appsettings.json");

        // A fresh store instance over the SAME db = an app restart.
        var reopened = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
        var notes = await reopened.GetNotesAsync("c1");

        notes.Should().HaveCount(2);
        notes.Select(n => n.Content).Should().ContainInOrder(
            "builds with dotnet build src/X.sln", "config lives in appsettings.json");
    }

    [Fact]
    public async Task Notes_Are_Conversation_Scoped_No_Cross_Leak()
    {
        await _store.AddNoteAsync("c1", "secret to A");
        await _store.AddNoteAsync("c2", "belongs to B");

        (await _store.GetNotesAsync("c1")).Should().ContainSingle().Which.Content.Should().Be("secret to A");
        (await _store.GetNotesAsync("c2")).Should().ContainSingle().Which.Content.Should().Be("belongs to B");
    }

    [Fact]
    public async Task Notes_Are_Capped_To_Newest_N_Per_Conversation()
    {
        for (var i = 0; i < 25; i++)
            await _store.AddNoteAsync("c1", $"note {i}");

        var notes = await _store.GetNotesAsync("c1");
        notes.Should().HaveCount(20);                 // MaxNotesPerConversation
        notes.Last().Content.Should().Be("note 24");  // newest kept
        notes.Should().NotContain(n => n.Content == "note 0"); // oldest pruned
    }

    [Fact]
    public async Task Long_Note_Is_Truncated_At_Store()
    {
        await _store.AddNoteAsync("c1", new string('x', 5000));
        (await _store.GetNotesAsync("c1")).Single().Content.Length.Should().BeLessThanOrEqualTo(2000);
    }

    [Fact]
    public async Task Deleting_A_Conversation_Removes_Its_Notes()
    {
        await _store.UpsertConversationAsync(new Conversation
        {
            Id = "c1", CreatedAt = DateTimeOffset.UtcNow, UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });
        await _store.AddNoteAsync("c1", "x");
        await _store.DeleteConversationAsync("c1");
        (await _store.GetNotesAsync("c1")).Should().BeEmpty();
    }

    // ── global scope (Global Memory) ──────────────────────────────────────────

    [Fact]
    public async Task Global_Notes_RoundTrip_And_Survive_A_Store_Reopen()
    {
        await _store.AddNoteAsync(IConversationStore.GlobalNotesId, "user prefers short reports");

        var reopened = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
        (await reopened.GetGlobalNotesAsync())
            .Should().ContainSingle().Which.Content.Should().Be("user prefers short reports");
        // Global notes never leak into a conversation's own list.
        (await reopened.GetNotesAsync("c1")).Should().BeEmpty();
    }

    [Fact]
    public async Task Global_Notes_Are_Capped_To_Newest_50()
    {
        for (var i = 0; i < 60; i++)
            await _store.AddNoteAsync(IConversationStore.GlobalNotesId, $"fact {i}");

        var notes = await _store.GetGlobalNotesAsync();
        notes.Should().HaveCount(50);
        notes.Last().Content.Should().Be("fact 59");             // newest kept
        notes.Should().NotContain(n => n.Content == "fact 9");   // oldest pruned
    }

    [Fact]
    public async Task DeleteNoteAsync_Removes_A_Single_Note()
    {
        await _store.AddNoteAsync(IConversationStore.GlobalNotesId, "keep");
        await _store.AddNoteAsync(IConversationStore.GlobalNotesId, "drop");
        var target = (await _store.GetGlobalNotesAsync()).Single(n => n.Content == "drop");

        await _store.DeleteNoteAsync(target.Id);

        (await _store.GetGlobalNotesAsync())
            .Should().ContainSingle().Which.Content.Should().Be("keep");
    }

    // ── formatter ─────────────────────────────────────────────────────────────

    [Fact]
    public void Formatter_Renders_A_Notes_Block_Newest_Within_Cap()
    {
        var notes = Enumerable.Range(0, 5).Select(i => Note($"fact {i}", i)).ToList();
        var block = AgentNotesFormatter.BuildBlock(notes);

        block.Should().NotBeNull();
        block.Should().Contain("## Notes");
        block.Should().Contain("fact 4"); // newest present
    }

    [Fact]
    public void Formatter_Is_Size_Bounded_Keeping_Newest()
    {
        var notes = Enumerable.Range(0, 50)
            .Select(i => Note($"fact {i} " + new string('y', 200), i)).ToList();

        var block = AgentNotesFormatter.BuildBlock(notes, maxChars: 500)!;

        block.Length.Should().BeLessThan(900); // header + capped body
        block.Should().Contain("fact 49");     // newest kept
        block.Should().Contain("omitted");     // some dropped
    }

    [Fact]
    public void Formatter_Returns_Null_For_No_Notes()
    {
        AgentNotesFormatter.BuildBlock(Array.Empty<AgentNote>()).Should().BeNull();
        AgentNotesFormatter.BuildBlock(Array.Empty<AgentNote>(), Array.Empty<AgentNote>()).Should().BeNull();
    }

    [Fact]
    public void Formatter_Renders_Global_Section_Before_Conversation_Section()
    {
        var convo = new[] { Note("task fact", 0) };
        var global = new[] { Note("user fact old", 0), Note("user fact new", 1) };

        var block = AgentNotesFormatter.BuildBlock(convo, global)!;

        block.Should().Contain("## Memory (global)");
        block.Should().Contain("## Notes (this conversation)");
        block.IndexOf("## Memory (global)", StringComparison.Ordinal)
            .Should().BeLessThan(block.IndexOf("## Notes (this conversation)", StringComparison.Ordinal));
        // Global notes render newest first.
        block.IndexOf("user fact new", StringComparison.Ordinal)
            .Should().BeLessThan(block.IndexOf("user fact old", StringComparison.Ordinal));
    }

    [Fact]
    public void Formatter_Renders_Global_Only_When_Conversation_Has_No_Notes()
    {
        var block = AgentNotesFormatter.BuildBlock(Array.Empty<AgentNote>(), new[] { Note("user fact", 0) });

        block.Should().NotBeNull();
        block.Should().Contain("## Memory (global)");
        block.Should().NotContain("## Notes (this conversation)");
    }

    [Fact]
    public void Formatter_Caps_Global_Section_Independently_Of_Conversation_Notes()
    {
        // A busy conversation must not evict global notes: each section has its own cap.
        var convo = Enumerable.Range(0, 50).Select(i => Note($"task {i} " + new string('z', 200), i)).ToList();
        var global = new[] { Note("user prefers short reports", 0) };

        var block = AgentNotesFormatter.BuildBlock(convo, global)!;

        block.Should().Contain("user prefers short reports");
        block.Should().Contain("task 49"); // newest conversation note still present
    }

    // ── tool ──────────────────────────────────────────────────────────────────

    [Fact]
    public async Task Remember_Tool_Persists_To_The_Active_Conversation()
    {
        var session = new ShellSession { CurrentConversationId = "c1" };
        var tool = new RememberTool(_store, session);
        tool.DefaultPermission.Should().Be(PermissionLevel.AllowAlways);

        var result = await tool.ExecuteAsync(Args(new { note = "prefers tabs" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        (await _store.GetNotesAsync("c1")).Should().ContainSingle().Which.Content.Should().Be("prefers tabs");
    }

    [Fact]
    public async Task Remember_Tool_Fails_Without_An_Active_Conversation()
    {
        var result = await new RememberTool(_store, new ShellSession())
            .ExecuteAsync(Args(new { note = "x" }), CancellationToken.None);
        result.Success.Should().BeFalse();
    }

    [Fact]
    public async Task Remember_Tool_Global_Scope_Persists_Even_Without_A_Conversation()
    {
        var result = await new RememberTool(_store, new ShellSession())
            .ExecuteAsync(Args(new { note = "user's team is Platform", scope = "global" }), CancellationToken.None);

        result.Success.Should().BeTrue();
        (await _store.GetGlobalNotesAsync())
            .Should().ContainSingle().Which.Content.Should().Be("user's team is Platform");
    }

    [Fact]
    public async Task Remember_Tool_Default_Scope_Stays_Conversation()
    {
        var session = new ShellSession { CurrentConversationId = "c1" };
        await new RememberTool(_store, session)
            .ExecuteAsync(Args(new { note = "task-local" }), CancellationToken.None);

        (await _store.GetGlobalNotesAsync()).Should().BeEmpty();
        (await _store.GetNotesAsync("c1")).Should().ContainSingle();
    }

    [Fact]
    public async Task Remember_Tool_Rejects_Unknown_Scope()
    {
        var result = await new RememberTool(_store, new ShellSession { CurrentConversationId = "c1" })
            .ExecuteAsync(Args(new { note = "x", scope = "everywhere" }), CancellationToken.None);
        result.Success.Should().BeFalse();
    }

    [Fact]
    public async Task Remember_Tool_Rejects_Empty_Note()
    {
        var result = await new RememberTool(_store, new ShellSession { CurrentConversationId = "c1" })
            .ExecuteAsync(Args(new { note = "" }), CancellationToken.None);
        result.Success.Should().BeFalse();
    }

    public void Dispose()
    {
        if (Directory.Exists(_dbDir))
            Directory.Delete(_dbDir, recursive: true);
    }
}
