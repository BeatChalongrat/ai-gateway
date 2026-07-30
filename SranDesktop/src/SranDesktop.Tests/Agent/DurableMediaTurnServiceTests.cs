using System.Text;
using System.Runtime.CompilerServices;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using Moq;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Domain.ValueObjects;
using SranDesktop.Infrastructure.Storage;
using Xunit;

namespace SranDesktop.Tests.Agent;

public sealed class DurableMediaTurnServiceTests : IDisposable
{
    private readonly string _directory =
        Path.Combine(Path.GetTempPath(), $"sran-media-runtime-{Guid.NewGuid():N}");
    private readonly string _databasePath;
    private readonly SqliteConversationStore _conversationStore;
    private readonly SqliteAgentTaskStore _store;
    private readonly DurableTaskRuntime _runtime;

    public DurableMediaTurnServiceTests()
    {
        _databasePath = Path.Combine(_directory, "data.db");
        _conversationStore = new SqliteConversationStore(_databasePath);
        _store = new SqliteAgentTaskStore(_databasePath);
        _runtime = new DurableTaskRuntime(
            _store,
            new GoalInterpreter(),
            new CapabilityRouter());
    }

    [Fact]
    public async Task Image_Turn_Creates_Verified_Durable_Action_Without_Persisting_Raw_Prompt()
    {
        const string secretPrompt =
            "draw a blue square using synthetic-secret-sk-test-phase3-123456789";
        var chat = new CapturingChatService();
        var security = new CapturingSecurityLogger();
        var service = new DurableMediaTurnService(chat, _runtime, security);
        await _conversationStore.UpsertConversationAsync(new Conversation
        {
            Id = "conversation-1",
            Title = "Media test",
            ModelId = "test",
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        });

        var events = await CollectAsync(service.RunAsync(
            "conversation-1",
            secretPrompt,
            ["attachment-id"],
            imageMode: true,
            videoMode: false));

        chat.Calls.Should().Be(
            1,
            string.Join(
                "; ",
                events.OfType<StreamErrorEvent>()
                    .Select(item => $"{item.Code}:{item.Message}")));
        chat.LastContent.Should().Be(secretPrompt);
        events.Should().ContainSingle(item => item is MessageFilesEvent);
        events.Should().ContainSingle(item => item is StreamCompletedEvent);
        events.Should().NotContain(item => item is StreamErrorEvent);
        _runtime.GetMetrics().TasksCreated.Should().Be(1);
        _runtime.GetMetrics().ActionsDispatched.Should().Be(1);
        _runtime.GetMetrics().ActionsVerified.Should().Be(1);
        security.Events.Should().Contain(item =>
            item.EventType == "media.egress.authorization"
            && item.PolicyDecision == "allow_once");
        security.Events.Should().Contain(item =>
            item.EventType == "media.execution"
            && item.PolicyDecision == "verified");
        security.Events.Select(value =>
                System.Text.Json.JsonSerializer.Serialize(value))
            .Should().OnlyContain(value => !value.Contains(secretPrompt, StringComparison.Ordinal));
        foreach (var path in Directory.EnumerateFiles(_directory))
        {
            var persisted = Encoding.UTF8.GetString(await File.ReadAllBytesAsync(path));
            persisted.Should().NotContain(secretPrompt);
        }
    }

    [Fact]
    public async Task Durable_Initialization_Failure_Blocks_Provider_Call()
    {
        var chat = new CapturingChatService();
        var runtime = new Mock<IDurableTaskRuntime>(MockBehavior.Strict);
        runtime.Setup(item => item.CreateRunAsync(
                It.IsAny<DurableRunRequest>(),
                It.IsAny<CancellationToken>()))
            .ThrowsAsync(new IOException("durable store unavailable"));
        var service = new DurableMediaTurnService(chat, runtime.Object);

        var events = await CollectAsync(service.RunAsync(
            "conversation-2",
            "draw a test image",
            null,
            imageMode: true,
            videoMode: false));

        chat.Calls.Should().Be(0);
        events.Should().ContainSingle().Which.Should().BeOfType<StreamErrorEvent>()
            .Which.Code.Should().Be("DurableRuntimeUnavailable");
    }

    private static async Task<List<ChatStreamEvent>> CollectAsync(
        IAsyncEnumerable<ChatStreamEvent> stream)
    {
        var result = new List<ChatStreamEvent>();
        await foreach (var item in stream)
            result.Add(item);
        return result;
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(_directory))
                Directory.Delete(_directory, recursive: true);
        }
        catch (IOException)
        {
        }
    }

    private sealed class CapturingChatService : IChatService
    {
        public int Calls { get; private set; }
        public string? LastContent { get; private set; }

        public async IAsyncEnumerable<ChatStreamEvent> SendMessageAsync(
            string conversationId,
            string content,
            IReadOnlyList<string>? fileIds = null,
            bool imageMode = false,
            bool videoMode = false,
            [EnumeratorCancellation] CancellationToken ct = default)
        {
            Calls++;
            LastContent = content;
            yield return new StreamStartedEvent("message-1");
            yield return new MessageFilesEvent(
            [
                new(
                    "media-1",
                    "generated.png",
                    "image/png",
                    128,
                    "https://provider.invalid/generated.png")
            ]);
            yield return new StreamCompletedEvent(new TokenUsage(1, 1, 2));
            await Task.CompletedTask;
        }
    }

    private sealed class CapturingSecurityLogger : ISecurityEventLogger
    {
        public List<SecurityAuditEvent> Events { get; } = [];

        public void Write(SecurityAuditEvent auditEvent) => Events.Add(auditEvent);
    }
}
