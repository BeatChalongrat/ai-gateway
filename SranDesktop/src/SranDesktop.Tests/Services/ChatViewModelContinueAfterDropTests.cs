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
using SranDesktop.Presentation.Localization;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Phase 5 Task 3 (D3): a mid-stream connection drop on the GATEWAY agent path
/// must offer the existing Continue affordance (the in-memory chain up to the
/// last completed step is intact) instead of auto-retrying — agent tools are
/// side-effectful, replaying them automatically is unsafe.
/// </summary>
public class ChatViewModelContinueAfterDropTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly ChatViewModel _vm;

    /// <summary>Swapped per test: what the next orchestrator run streams.</summary>
    private Func<IAsyncEnumerable<AgentStreamEvent>> _nextReply =
        () => ReplyWith("unused");

    public ChatViewModelContinueAfterDropTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-drop-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));

        var skills = new Mock<ISkillManager>();
        skills.Setup(s => s.FindMatchingSkillsAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Array.Empty<Skill>());

        var orchestrator = new Mock<IChatOrchestrator>();
        orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(() => _nextReply());

        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());

        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListMessagesAsync(
                It.IsAny<string>(), It.IsAny<int>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MessageListResponse(false, new List<MessageDto>()));

        _vm = new ChatViewModel(
            api.Object,
            Mock.Of<IGatewayApiClient>(),
            _store,
            Mock.Of<IFileService>(),
            orchestrator.Object,
            Mock.Of<IChatService>(),
            skills.Object,
            Mock.Of<ISpeechService>(),
            settings.Object,
            Mock.Of<IAgentConversationSyncService>(),
            NullLogger<ChatViewModel>.Instance);
    }

    private static async IAsyncEnumerable<AgentStreamEvent> ReplyWith(string text)
    {
        yield return new AgentContentDeltaEvent(text);
        yield return new AgentStreamDoneEvent();
        await Task.CompletedTask;
    }

    /// <summary>Streams the given text (when non-null), then drops the connection.</summary>
    private static async IAsyncEnumerable<AgentStreamEvent> DropAfter(string? text)
    {
        await Task.Yield();
        if (text is not null)
            yield return new AgentContentDeltaEvent(text);
        throw new IOException("connection dropped mid-stream");
    }

    private static async IAsyncEnumerable<AgentStreamEvent> ServerRefusal()
    {
        await Task.Yield();
        throw new HttpRequestException("upstream said no", null, System.Net.HttpStatusCode.BadGateway);
#pragma warning disable CS0162
        yield break;
#pragma warning restore CS0162
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

    private async Task SendDroppedTurnAsync(string? partial)
    {
        _nextReply = () => DropAfter(partial);
        _vm.Conversation = await SeedConversationAsync();
        _vm.InputText = "do the long task";
        await _vm.SendCommand.ExecuteAsync(null);
    }

    [Fact]
    public async Task Connection_Drop_Enables_Continue()
    {
        await SendDroppedTurnAsync("partial ");

        var bubble = _vm.Messages.Last(m => m.IsAssistant);
        bubble.IsError.Should().BeTrue("the drop still surfaces as an error");
        bubble.Content.Should().Be("partial ", "streamed text is kept");
        bubble.CanContinue.Should().BeTrue();
        bubble.MetaLine.Should().Be(LocaleService.Instance.T("status.continueAvailable"));
        _vm.ContinueTurnCommand.CanExecute(null).Should().BeTrue();
    }

    [Fact]
    public async Task Continue_After_Drop_Resumes_From_The_Partial_Reply()
    {
        await SendDroppedTurnAsync("partial ");

        _nextReply = () => ReplyWith("resumed answer");
        await _vm.ContinueTurnCommand.ExecuteAsync(null);

        var bubble = _vm.Messages.Last(m => m.IsAssistant);
        bubble.IsError.Should().BeFalse("the resumed turn completed normally");
        bubble.Content.Should().Be("partial resumed answer");
        bubble.CanContinue.Should().BeFalse();
        _vm.ContinueTurnCommand.CanExecute(null).Should().BeFalse("the resume state is consumed");
    }

    [Fact]
    public async Task Drop_Before_Any_Content_Clears_The_Injected_Error_Text_On_Continue()
    {
        // With nothing streamed yet SetError writes the error sentence INTO the
        // bubble; a Continue must clear it so the resumed reply doesn't append
        // after an error message.
        await SendDroppedTurnAsync(null);

        var bubble = _vm.Messages.Last(m => m.IsAssistant);
        bubble.Content.Should().Be(LocaleService.Instance.T("error.connectionLost"));
        bubble.CanContinue.Should().BeTrue();
        _vm.ContinueTurnCommand.CanExecute(null).Should().BeTrue();

        _nextReply = () => ReplyWith("fresh answer");
        await _vm.ContinueTurnCommand.ExecuteAsync(null);

        bubble.Content.Should().Be("fresh answer");
        bubble.IsError.Should().BeFalse();
    }

    [Fact]
    public async Task Server_Errors_Do_Not_Enable_Continue()
    {
        // Only a transport drop is resumable; an HTTP-level refusal keeps the
        // existing behaviour (error, no Continue).
        _nextReply = ServerRefusal;
        _vm.Conversation = await SeedConversationAsync();
        _vm.InputText = "do the long task";
        await _vm.SendCommand.ExecuteAsync(null);

        var bubble = _vm.Messages.Last(m => m.IsAssistant);
        bubble.IsError.Should().BeTrue();
        bubble.CanContinue.Should().BeFalse();
        _vm.ContinueTurnCommand.CanExecute(null).Should().BeFalse();
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
