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
/// P1 focus fix: every terminal turn path (complete / cancelled / errored)
/// raises ComposerFocusRequested exactly once, flagged OnlyIfIdle so the view
/// refocuses the composer only when the user's focus is not already somewhere
/// useful. The pre-existing unconditional callers (edit-message / suggestion
/// chips / New chat, all via RequestComposerFocus) keep raising WITHOUT the
/// flag. The view-side "is focus already somewhere useful?" guard lives in
/// ComposerControl (visual tree) and is not headless-testable here.
/// </summary>
public class ChatViewModelComposerFocusTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly Mock<IWindowStateProvider> _windowState = new();
    private readonly Mock<IChatOrchestrator> _orchestrator = new();

    public ChatViewModelComposerFocusTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-focus-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));

        // Focused window: the completion balloon stays quiet; the focus event
        // must fire regardless of window state (the view applies the guard).
        _windowState.SetupGet(w => w.IsWindowActive).Returns(true);
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
            completionNotifier: Mock.Of<ICompletionNotifier>(),
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

    private static (List<EventArgs> raised, ChatViewModel vm) Track(ChatViewModel vm)
    {
        var raised = new List<EventArgs>();
        vm.ComposerFocusRequested += (_, e) => raised.Add(e);
        return (raised, vm);
    }

    [Fact]
    public async Task CompletedTurn_RaisesOnlyIfIdleFocusRequest_ExactlyOnce()
    {
        var (raised, vm) = Track(BuildVm());
        vm.Conversation = await SeedConversationAsync();
        vm.InputText = "hello agent";

        await vm.SendCommand.ExecuteAsync(null);

        raised.Should().ContainSingle()
            .Which.Should().BeOfType<ComposerFocusRequestedEventArgs>()
            .Which.OnlyIfIdle.Should().BeTrue();
    }

    [Fact]
    public async Task CancelledTurn_RaisesOnlyIfIdleFocusRequest_ExactlyOnce()
    {
        _orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(() => FailWith(new OperationCanceledException()));
        var (raised, vm) = Track(BuildVm());
        vm.Conversation = await SeedConversationAsync();
        vm.InputText = "hello agent";

        await vm.SendCommand.ExecuteAsync(null);

        raised.Should().ContainSingle()
            .Which.Should().BeOfType<ComposerFocusRequestedEventArgs>()
            .Which.OnlyIfIdle.Should().BeTrue();
    }

    [Fact]
    public async Task ErroredTurn_RaisesOnlyIfIdleFocusRequest_ExactlyOnce()
    {
        _orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(() => FailWith(new HttpRequestException("boom")));
        var (raised, vm) = Track(BuildVm());
        vm.Conversation = await SeedConversationAsync();
        vm.InputText = "hello agent";

        await vm.SendCommand.ExecuteAsync(null);

        vm.Messages.Last(m => m.IsAssistant).IsError.Should().BeTrue();
        raised.Should().ContainSingle()
            .Which.Should().BeOfType<ComposerFocusRequestedEventArgs>()
            .Which.OnlyIfIdle.Should().BeTrue();
    }

    [Fact]
    public void RequestComposerFocus_StaysUnconditional()
    {
        // The pre-existing entry point (suggestion chips / New chat) must keep
        // its semantics: raised without the OnlyIfIdle flavor so the view
        // focuses unconditionally.
        var (raised, vm) = Track(BuildVm());

        vm.RequestComposerFocus();

        raised.Should().ContainSingle()
            .Which.Should().NotBeOfType<ComposerFocusRequestedEventArgs>();
    }

    [Fact]
    public void EditMessage_StillRaisesUnconditionalFocusRequest()
    {
        var (raised, vm) = Track(BuildVm());
        var userMessage = new MessageViewModel(
            "m1", MessageRole.User, "original text", DateTimeOffset.UtcNow);

        vm.EditMessageCommand.Execute(userMessage);

        vm.InputText.Should().Be("original text");
        raised.Should().ContainSingle()
            .Which.Should().NotBeOfType<ComposerFocusRequestedEventArgs>();
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
