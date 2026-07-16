using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>Durable tool history (Phase A PART 1): the chain ⇄ rows codec and a
/// store round-trip proving tool_calls / tool results / screenshot placeholders
/// persist and regroup.</summary>
public class AgentChainCodecTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;

    public AgentChainCodecTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-chain-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
    }

    private static List<GatewayMessage> SampleChain() => new()
    {
        new GatewayMessage
        {
            Role = "assistant", Content = "Let me read that file.",
            ToolCalls = new List<GatewayToolCall>
            {
                new()
                {
                    Id = "call_1",
                    Function = new GatewayToolCallFunction { Name = "read_file", Arguments = """{"path":"x.txt"}""" }
                }
            }
        },
        new GatewayMessage { Role = "tool", Content = "file contents here", ToolCallId = "call_1", Name = "read_file" },
        ScreenshotMessage.Create("computer_screenshot", "data:image/png;base64,IMGDATA"),
    };

    [Fact]
    public void Sanitize_Collapses_Screenshots_To_Placeholders()
    {
        var sanitized = AgentChainCodec.Sanitize(SampleChain());

        sanitized.Should().HaveCount(3);
        ScreenshotMessage.IsScreenshotMessage(sanitized[2]).Should().BeFalse();
        (sanitized[2].Content as string).Should().Be(AgentChainCodec.ScreenshotPlaceholderText);
        // Non-screenshot messages pass through untouched.
        sanitized[0].ToolCalls.Should().ContainSingle(tc => tc.Id == "call_1");
    }

    [Fact]
    public void IsChainRow_Classifies_Tool_Chain_Vs_Bubbles()
    {
        var rows = AgentChainCodec.ToRows("c1", "asst1", AgentChainCodec.Sanitize(SampleChain()),
            DateTimeOffset.UtcNow, 1);

        rows.Should().OnlyContain(r => AgentChainCodec.IsChainRow(r));

        var userBubble = new Message
        {
            Id = "u1", ConversationId = "c1", Role = MessageRole.User,
            Content = "hello", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 0
        };
        var finalAnswer = new Message
        {
            Id = "asst1", ConversationId = "c1", Role = MessageRole.Assistant,
            Content = "done", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 5
        };
        AgentChainCodec.IsChainRow(userBubble).Should().BeFalse();
        AgentChainCodec.IsChainRow(finalAnswer).Should().BeFalse();
    }

    [Fact]
    public void Chain_RoundTrips_Through_Rows_And_Regroups()
    {
        var sanitized = AgentChainCodec.Sanitize(SampleChain());
        var chainRows = AgentChainCodec.ToRows("c1", "asst1", sanitized, DateTimeOffset.UtcNow, 1);

        // Simulate a full persisted turn: user, chain rows, final answer.
        var rows = new List<Message>
        {
            new() { Id = "u1", ConversationId = "c1", Role = MessageRole.User, Content = "read x", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 0 },
        };
        rows.AddRange(chainRows);
        rows.Add(new Message { Id = "asst1", ConversationId = "c1", Role = MessageRole.Assistant, Content = "here it is", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 5 });

        var map = AgentChainCodec.GroupChains(rows);

        map.Should().ContainKey("asst1");
        var chain = map["asst1"];
        chain.Should().HaveCount(3);
        chain[0].Role.Should().Be("assistant");
        chain[0].ToolCalls.Should().ContainSingle(tc => tc.Id == "call_1"
            && tc.Function.Name == "read_file");
        chain[1].Role.Should().Be("tool");
        chain[1].ToolCallId.Should().Be("call_1");
        chain[2].Role.Should().Be("user");
        (chain[2].Content as string).Should().Contain("screenshot");
    }

    [Fact]
    public void GroupChains_Ignores_Turns_Without_Tools()
    {
        var rows = new List<Message>
        {
            new() { Id = "u1", ConversationId = "c1", Role = MessageRole.User, Content = "hi", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 0 },
            new() { Id = "a1", ConversationId = "c1", Role = MessageRole.Assistant, Content = "hello", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 1 },
        };

        AgentChainCodec.GroupChains(rows).Should().BeEmpty();
    }

    [Fact]
    public async Task Tool_Chain_Persists_And_Reloads_Through_The_Store()
    {
        await _store.UpsertConversationAsync(new Conversation
        {
            Id = "c1", Title = "t", CreatedAt = DateTimeOffset.UtcNow, UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });

        var rows = new List<Message>
        {
            new() { Id = "u1", ConversationId = "c1", Role = MessageRole.User, Content = "read x", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 0 },
        };
        rows.AddRange(AgentChainCodec.ToRows("c1", "asst1",
            AgentChainCodec.Sanitize(SampleChain()), DateTimeOffset.UtcNow, 1));
        rows.Add(new Message { Id = "asst1", ConversationId = "c1", Role = MessageRole.Assistant, Content = "here it is", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 5 });

        await _store.UpsertMessagesAsync(rows);

        // A store round-trip (as after a restart) preserves the full chain.
        var reloaded = await _store.GetMessagesAsync("c1");
        reloaded.Should().HaveCount(5);
        reloaded.Should().Contain(m => m.Role == MessageRole.Tool && m.ToolCallId == "call_1" && m.Name == "read_file");
        reloaded.Should().Contain(m => m.Role == MessageRole.Assistant && !string.IsNullOrEmpty(m.ToolCallsJson));

        var map = AgentChainCodec.GroupChains(reloaded);
        map.Should().ContainKey("asst1");
        map["asst1"].Should().HaveCount(3);
    }

    [Fact]
    public async Task Re_Persisting_A_Resumed_Turn_Upserts_Not_Duplicates()
    {
        await _store.UpsertConversationAsync(new Conversation
        {
            Id = "c1", Title = "t", CreatedAt = DateTimeOffset.UtcNow, UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });

        // First (paused) persist: user + 3 chain rows, no final answer yet.
        var first = new List<Message>
        {
            new() { Id = "u1", ConversationId = "c1", Role = MessageRole.User, Content = "go", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 0 },
        };
        first.AddRange(AgentChainCodec.ToRows("c1", "asst1",
            AgentChainCodec.Sanitize(SampleChain()), DateTimeOffset.UtcNow, 1));
        await _store.UpsertMessagesAsync(first);

        // Continue: chain grew by one tool result; now with a final answer. The
        // shared -tc ids upsert instead of creating duplicate rows.
        var grown = AgentChainCodec.Sanitize(SampleChain());
        grown.Add(new GatewayMessage { Role = "tool", Content = "more", ToolCallId = "call_2", Name = "read_file" });
        var second = new List<Message>();
        second.AddRange(AgentChainCodec.ToRows("c1", "asst1", grown, DateTimeOffset.UtcNow, 10));
        second.Add(new Message { Id = "asst1", ConversationId = "c1", Role = MessageRole.Assistant, Content = "final", CreatedAt = DateTimeOffset.UtcNow, SortOrder = 20 });
        await _store.UpsertMessagesAsync(second);

        var reloaded = await _store.GetMessagesAsync("c1");
        // user + 4 chain rows + final answer = 6 (no duplicated -tc0..-tc2).
        reloaded.Should().HaveCount(6);
        reloaded.Count(m => m.Id.StartsWith("asst1-tc")).Should().Be(4);
    }

    public void Dispose()
    {
        Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
        if (Directory.Exists(_dbDir))
            Directory.Delete(_dbDir, recursive: true);
    }
}
