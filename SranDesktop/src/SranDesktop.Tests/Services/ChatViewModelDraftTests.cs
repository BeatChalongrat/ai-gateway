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

/// <summary>Phase 3 C4: the composer draft lifecycle against a REAL temp
/// SQLite store — flush on demand, restore-free send path, clear on send.</summary>
public class ChatViewModelDraftTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly ChatViewModel _vm;

    public ChatViewModelDraftTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-draft-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));

        var skills = new Mock<ISkillManager>();
        skills.Setup(s => s.FindMatchingSkillsAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Array.Empty<Skill>());

        var orchestrator = new Mock<IChatOrchestrator>();
        orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(DoneOnly());

        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());

        // OpenConversationAsync loads server history + state — give the loose
        // mock real (empty) payloads so awaited calls don't see null Tasks.
        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListMessagesAsync(
                It.IsAny<string>(), It.IsAny<int>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MessageListResponse(false, new List<MessageDto>()));
        api.Setup(a => a.GetConversationAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ConversationDto(
                "c1", "My chat", "gpt-4o-mini", 0,
                DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow, false, null));

        _vm = new ChatViewModel(
            api.Object,
            Mock.Of<IGatewayApiClient>(),
            _store,
            Mock.Of<IFileService>(),
            orchestrator.Object,
            Mock.Of<IDurableMediaTurnService>(),
            skills.Object,
            Mock.Of<ISpeechService>(),
            settings.Object,
            Mock.Of<IAgentConversationSyncService>(),
            NullLogger<ChatViewModel>.Instance);
    }

    private static async IAsyncEnumerable<AgentStreamEvent> DoneOnly()
    {
        yield return new AgentStreamDoneEvent();
        await Task.CompletedTask;
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
    public async Task FlushDraft_Persists_Current_Composer_Text()
    {
        _vm.Conversation = await SeedConversationAsync();
        _vm.InputText = "half-typed thought";

        await _vm.FlushDraftAsync();

        (await _store.GetDraftAsync("c1")).Should().Be("half-typed thought");
    }

    [Fact]
    public async Task Send_Clears_The_Draft()
    {
        _vm.Conversation = await SeedConversationAsync();
        await _store.SaveDraftAsync("c1", "stale draft");
        _vm.InputText = "hello agent";

        await _vm.SendCommand.ExecuteAsync(null);

        (await _store.GetDraftAsync("c1")).Should().BeNull();
        _vm.InputText.Should().BeEmpty();
    }

    [Fact]
    public async Task Opening_A_Conversation_Restores_Its_Draft()
    {
        var conversation = await SeedConversationAsync();
        await _store.SaveDraftAsync("c1", "restore me");

        await _vm.OpenConversationAsync(conversation);

        _vm.InputText.Should().Be("restore me");
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
