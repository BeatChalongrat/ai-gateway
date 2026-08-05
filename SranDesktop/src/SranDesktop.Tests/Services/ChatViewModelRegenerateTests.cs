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

/// <summary>Phase-1 quick-win fix: Regenerate must FORK the last turn via
/// the branch infra (old reply preserved as a ◀ n/m ▶ branch) instead of
/// appending a duplicate user bubble. Also covers the errored-turn retry
/// path, which reuses the same command.</summary>
public class ChatViewModelRegenerateTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly ChatViewModel _vm;

    public ChatViewModelRegenerateTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-regen-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));

        var skills = new Mock<ISkillManager>();
        skills.Setup(s => s.FindMatchingSkillsAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Array.Empty<Skill>());

        var orchestrator = new Mock<IChatOrchestrator>();
        orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Returns(() => ReplyWith("fresh answer"));

        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());

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
    public async Task Regenerate_Does_Not_Duplicate_The_User_Message()
    {
        _vm.Conversation = await SeedConversationAsync();
        _vm.InputText = "hello agent";
        await _vm.SendCommand.ExecuteAsync(null);

        _vm.Messages.Count(m => m.IsUser).Should().Be(1, "the first send adds one user bubble");

        await _vm.RegenerateCommand.ExecuteAsync(null);

        // The visible transcript must still show exactly ONE user bubble
        // with that text — the pre-fix behavior appended a second one.
        _vm.Messages.Count(m => m.IsUser && m.Content == "hello agent").Should().Be(1);
    }

    [Fact]
    public async Task Regenerate_Forks_The_Old_Reply_As_A_Branch()
    {
        _vm.Conversation = await SeedConversationAsync();
        _vm.InputText = "hello agent";
        await _vm.SendCommand.ExecuteAsync(null);

        await _vm.RegenerateCommand.ExecuteAsync(null);

        var head = _vm.Messages.First(m => m.IsUser);
        head.BranchCount.Should().Be(2, "old tail + regenerated tail");
        head.BranchIndex.Should().Be(1, "the fresh reply is the active branch");
        head.HasBranches.Should().BeTrue();
    }

    [Fact]
    public async Task Regenerate_On_An_Errored_Turn_Retries_Without_Duplicates()
    {
        _vm.Conversation = await SeedConversationAsync();
        _vm.InputText = "hello agent";
        await _vm.SendCommand.ExecuteAsync(null);

        // Simulate the failed turn the Retry pill targets.
        var failed = _vm.Messages.Last(m => m.IsAssistant);
        failed.IsError = true;

        await _vm.RegenerateCommand.ExecuteAsync(null);

        _vm.Messages.Count(m => m.IsUser && m.Content == "hello agent").Should().Be(1);
        // The active branch's reply is the fresh (non-error) one.
        _vm.Messages.Last(m => m.IsAssistant).IsError.Should().BeFalse();
    }

    [Fact]
    public async Task Regenerate_Without_A_User_Message_Is_A_NoOp()
    {
        _vm.Conversation = await SeedConversationAsync();

        await _vm.RegenerateCommand.ExecuteAsync(null);

        _vm.Messages.Should().BeEmpty();
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
