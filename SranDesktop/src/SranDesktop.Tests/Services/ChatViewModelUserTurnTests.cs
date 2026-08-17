using FluentAssertions;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// P3 — UserTurnStarted is the view's cue to anchor the new turn near the
/// viewport top. It must fire exactly once per user send (before the reply
/// streams), and never from non-send message activity, so incoming/remote
/// rows can never move the reader's viewport.
/// </summary>
public class ChatViewModelUserTurnTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly Mock<IChatOrchestrator> _orchestrator = new();

    public ChatViewModelUserTurnTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-turn-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
        _orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(ReplyWith);
    }

    private ChatViewModel BuildVm()
    {
        var skills = new Mock<ISkillManager>();
        skills.Setup(s => s.FindMatchingSkillsAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Array.Empty<Skill>());
        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());
        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListMessagesAsync(
                It.IsAny<string>(), It.IsAny<int>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MessageListResponse(false, new List<MessageDto>()));

        return new ChatViewModel(
            api.Object,
            Mock.Of<IGatewayApiClient>(),
            _store,
            Mock.Of<IFileService>(),
            _orchestrator.Object,
            Mock.Of<IChatService>(),
            skills.Object,
            Mock.Of<ISpeechService>(),
            settings.Object,
            Mock.Of<IAgentConversationSyncService>(),
            NullLogger<ChatViewModel>.Instance,
            completionNotifier: Mock.Of<ICompletionNotifier>(),
            windowState: Mock.Of<IWindowStateProvider>());
    }

    private static async IAsyncEnumerable<AgentStreamEvent> ReplyWith()
    {
        yield return new AgentContentDeltaEvent("reply token one ");
        yield return new AgentContentDeltaEvent("reply token two");
        yield return new AgentStreamDoneEvent();
        await Task.CompletedTask;
    }

    private async Task<ConversationItemViewModel> SeedConversationAsync(string id = "c1")
    {
        var entity = new Conversation
        {
            Id = id,
            Title = "My chat",
            ModelId = "gpt-4o-mini",
            CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-5),
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        };
        await _store.UpsertConversationAsync(entity);
        return new ConversationItemViewModel(entity);
    }

    [Fact]
    public async Task Send_RaisesUserTurnStarted_ExactlyOnce_BeforeTheReplyLands()
    {
        var vm = BuildVm();
        vm.Conversation = await SeedConversationAsync();
        vm.InputText = "hello";
        var raised = 0;
        var userVisibleAtRaise = false;
        var replyEmptyAtRaise = false;
        vm.UserTurnStarted += (_, _) =>
        {
            raised++;
            userVisibleAtRaise = vm.Messages.Any(m => m.IsUser);
            replyEmptyAtRaise =
                vm.Messages.LastOrDefault(m => m.IsAssistant)?.Content.Length == 0;
        };

        await vm.SendCommand.ExecuteAsync(null);

        raised.Should().Be(1, "one send = one anchor cue, streamed deltas add none");
        userVisibleAtRaise.Should().BeTrue("the anchor target must already be in Messages");
        replyEmptyAtRaise.Should().BeTrue("the cue fires before the reply streams");
    }

    [Fact]
    public async Task MessageActivity_FiresManyTimes_ButUserTurnOnlyOnce()
    {
        var vm = BuildVm();
        vm.Conversation = await SeedConversationAsync();
        vm.InputText = "hello";
        var turns = 0;
        var activity = 0;
        vm.UserTurnStarted += (_, _) => turns++;
        vm.MessageActivity += (_, _) => activity++;

        await vm.SendCommand.ExecuteAsync(null);

        turns.Should().Be(1);
        activity.Should().BeGreaterThan(turns,
            "streamed deltas fire activity without re-anchoring");
    }

    public void Dispose()
    {
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
