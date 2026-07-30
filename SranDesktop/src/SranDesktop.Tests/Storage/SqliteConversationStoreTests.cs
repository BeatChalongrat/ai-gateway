using FluentAssertions;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Domain.ValueObjects;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Storage;

public class SqliteConversationStoreTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;

    public SqliteConversationStoreTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-db-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
    }

    private static Conversation NewConversation(string id = "c1") => new()
    {
        Id = id,
        Title = "Test chat",
        ModelId = "gpt-4o",
        CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-5),
        UpdatedAt = DateTimeOffset.UtcNow,
        Source = ConversationSource.Server
    };

    [Fact]
    public async Task Conversation_Upsert_And_Get_RoundTrips()
    {
        var convo = NewConversation();
        await _store.UpsertConversationAsync(convo);

        var loaded = await _store.GetConversationAsync("c1");

        loaded.Should().NotBeNull();
        loaded!.Title.Should().Be("Test chat");
        loaded.ModelId.Should().Be("gpt-4o");
        loaded.Source.Should().Be(ConversationSource.Server);
    }

    [Fact]
    public async Task Conversation_Upsert_Twice_Updates()
    {
        var convo = NewConversation();
        await _store.UpsertConversationAsync(convo);
        convo.Title = "Renamed";
        await _store.UpsertConversationAsync(convo);

        var all = await _store.GetConversationsAsync();

        all.Should().HaveCount(1);
        all[0].Title.Should().Be("Renamed");
    }

    [Fact]
    public async Task Messages_RoundTrip_With_TokenUsage_And_Order()
    {
        await _store.UpsertConversationAsync(NewConversation());
        await _store.UpsertMessageAsync(new Message
        {
            Id = "m2", ConversationId = "c1", Role = MessageRole.Assistant,
            Content = "Hello **world**", ModelId = "gpt-4o",
            TokenUsage = new TokenUsage(10, 20, 30),
            CreatedAt = DateTimeOffset.UtcNow, SortOrder = 1
        });
        await _store.UpsertMessageAsync(new Message
        {
            Id = "m1", ConversationId = "c1", Role = MessageRole.User,
            Content = "hi", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 0
        });

        var messages = await _store.GetMessagesAsync("c1");

        messages.Should().HaveCount(2);
        messages[0].Id.Should().Be("m1");
        messages[1].TokenUsage.Should().Be(new TokenUsage(10, 20, 30));
        messages[1].Role.Should().Be(MessageRole.Assistant);
    }

    [Fact]
    public async Task Delete_Conversation_Cascades_To_Messages()
    {
        await _store.UpsertConversationAsync(NewConversation());
        await _store.UpsertMessageAsync(new Message
        {
            Id = "m1", ConversationId = "c1", Role = MessageRole.User,
            Content = "hi", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 0
        });

        await _store.DeleteConversationAsync("c1");

        (await _store.GetConversationsAsync()).Should().BeEmpty();
        (await _store.GetMessagesAsync("c1")).Should().BeEmpty();
    }

    [Fact]
    public async Task NextSortOrder_Starts_At_Zero_And_Increments()
    {
        await _store.UpsertConversationAsync(NewConversation());

        (await _store.GetNextSortOrderAsync("c1")).Should().Be(0);

        await _store.UpsertMessageAsync(new Message
        {
            Id = "m1", ConversationId = "c1", Role = MessageRole.User,
            Content = "hi", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 0
        });

        (await _store.GetNextSortOrderAsync("c1")).Should().Be(1);
    }

    [Fact]
    public async Task ToolPermissions_Upsert_And_Get()
    {
        await _store.SetToolPermissionAsync("read_file", "global", "ask");
        await _store.SetToolPermissionAsync("read_file", "global", "allow_always");

        var permission = await _store.GetToolPermissionAsync("read_file", "global");

        permission.Should().Be("allow_always");
    }

    public void Dispose()
    {
        if (Directory.Exists(_dbDir))
            Directory.Delete(_dbDir, recursive: true);
    }
}
