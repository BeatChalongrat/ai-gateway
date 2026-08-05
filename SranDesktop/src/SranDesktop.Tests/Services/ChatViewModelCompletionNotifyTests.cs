using System.Net.Http;
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
/// OS-level completion notification gate: a finished agent turn fires the
/// notifier exactly once when the main window is unfocused at completion time
/// (minimized / hidden to tray / behind another app), stays silent when the
/// window is focused, and carries the error flavor for errored turns.
/// </summary>
public class ChatViewModelCompletionNotifyTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly Mock<ICompletionNotifier> _notifier = new();
    private readonly Mock<IWindowStateProvider> _windowState = new();
    private readonly Mock<IChatOrchestrator> _orchestrator = new();

    public ChatViewModelCompletionNotifyTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-notify-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));

        _orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(() => ReplyWith("done"));
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
            completionNotifier: _notifier.Object,
            windowState: _windowState.Object);
    }

    private static async IAsyncEnumerable<AgentStreamEvent> ReplyWith(string text)
    {
        yield return new AgentContentDeltaEvent(text);
        yield return new AgentStreamDoneEvent();
        await Task.CompletedTask;
    }

    /// <summary>An agent stream that fails immediately — the iterator shape
    /// (conditional throw + yield break) keeps the compiler happy.</summary>
    private static async IAsyncEnumerable<AgentStreamEvent> FailWith(Exception ex)
    {
        await Task.CompletedTask;
        if (ex is not null)
            throw ex;
        yield break;
    }

    private async Task<ConversationItemViewModel> SeedConversationAsync(string id = "c1")
    {
        var entity = new Conversation
        {
            Id = id,
            Title = "My chat", // titled → no title-generation round-trip in SendAsync
            ModelId = "gpt-4o-mini",
            CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-5),
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        };
        await _store.UpsertConversationAsync(entity);
        return new ConversationItemViewModel(entity);
    }

    [Fact]
    public async Task CompletedTurn_WindowUnfocused_NotifiesOnce()
    {
        _windowState.SetupGet(w => w.IsWindowActive).Returns(false);
        var vm = BuildVm();
        vm.Conversation = await SeedConversationAsync();
        vm.InputText = "hello agent";

        await vm.SendCommand.ExecuteAsync(null);

        _notifier.Verify(
            n => n.NotifyTurnCompleted("c1", "My chat", false), Times.Once);
        _notifier.VerifyNoOtherCalls();
    }

    [Fact]
    public async Task CompletedTurn_WindowFocused_DoesNotNotify()
    {
        _windowState.SetupGet(w => w.IsWindowActive).Returns(true);
        var vm = BuildVm();
        vm.Conversation = await SeedConversationAsync();
        vm.InputText = "hello agent";

        await vm.SendCommand.ExecuteAsync(null);

        vm.Messages.Last(m => m.IsAssistant).Content.Should().Be("done",
            "the turn itself must still complete normally");
        _notifier.Verify(
            n => n.NotifyTurnCompleted(It.IsAny<string>(), It.IsAny<string?>(), It.IsAny<bool>()),
            Times.Never);
    }

    [Fact]
    public async Task ErroredTurn_WindowUnfocused_NotifiesWithErrorFlavor()
    {
        _windowState.SetupGet(w => w.IsWindowActive).Returns(false);
        _orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(() => FailWith(new HttpRequestException("boom")));
        var vm = BuildVm();
        vm.Conversation = await SeedConversationAsync();
        vm.InputText = "hello agent";

        await vm.SendCommand.ExecuteAsync(null);

        vm.Messages.Last(m => m.IsAssistant).IsError.Should().BeTrue();
        _notifier.Verify(
            n => n.NotifyTurnCompleted("c1", "My chat", true), Times.Once);
        _notifier.VerifyNoOtherCalls();
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
