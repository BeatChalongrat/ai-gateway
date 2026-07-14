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

/// <summary>Phase 10 D2: image attachments are scoped to the turn that sent
/// them. An image-mode turn (chat-edge path) with attachments must leave NO
/// pending image state behind — the next normal gateway turn's request
/// carries plain string content, not stale vision blocks.</summary>
public class ChatViewModelImageTurnScopeTests : IDisposable
{
    // Valid 1x1 PNG so InlineImages / data-uri building would genuinely fire
    // if stale paths ever reached the gateway turn builder.
    private static readonly byte[] OnePixelPng = Convert.FromBase64String(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==");

    private readonly string _dbDir;
    private readonly string _imagePath;
    private readonly SqliteConversationStore _store;
    private readonly ChatViewModel _vm;
    private readonly List<List<GatewayMessage>> _gatewayTurns = new();

    public ChatViewModelImageTurnScopeTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-imgscope-{Guid.NewGuid():N}");
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));
        _imagePath = Path.Combine(_dbDir, "attached.png");
        File.WriteAllBytes(_imagePath, OnePixelPng);

        var skills = new Mock<ISkillManager>();
        skills.Setup(s => s.FindMatchingSkillsAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Array.Empty<Skill>());

        // Gateway path: capture each turn's outgoing message list.
        var orchestrator = new Mock<IChatOrchestrator>();
        orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            .Callback((string _, List<GatewayMessage> messages, string _, int? _, CancellationToken _) =>
                _gatewayTurns.Add(messages))
            .Returns(DoneOnly());

        // Chat-edge path (image-mode turns): one delta, then the stream ends.
        var chatService = new Mock<IChatService>();
        chatService.Setup(c => c.SendMessageAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<IReadOnlyList<string>?>(),
                It.IsAny<bool>(), It.IsAny<bool>(), It.IsAny<CancellationToken>()))
            .Returns(DeltaOnly());

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
            chatService.Object,
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

    private static async IAsyncEnumerable<ChatStreamEvent> DeltaOnly()
    {
        yield return new ContentDeltaEvent("generated!");
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
    public async Task Normal_Turn_After_ImageMode_Turn_With_Attachment_Carries_No_Image_Blocks()
    {
        // Attach while no conversation is open (files go "ready" without an
        // upload), then open the conversation — mirroring the home-screen flow.
        await _vm.AttachFilesAsync(new[] { _imagePath });
        _vm.Conversation = await SeedConversationAsync();

        // Turn 1: IMAGE MODE with an attached image → routes via chat-edge,
        // which never consumes image paths.
        _vm.IsImageModeOn = true;
        _vm.InputText = "draw a cat like this";
        await _vm.SendCommand.ExecuteAsync(null);

        _gatewayTurns.Should().BeEmpty("an image-mode turn streams via chat-edge, not the gateway");
        _vm.IsImageModeOn.Should().BeFalse("the toggle is per-message");

        // Turn 2: normal gateway turn, no attachments.
        _vm.InputText = "now describe what you made";
        await _vm.SendCommand.ExecuteAsync(null);

        _gatewayTurns.Should().HaveCount(1);
        var lastUser = _gatewayTurns[0].Last(m => m.Role == "user");
        lastUser.Content.Should().BeOfType<string>(
            "a turn without image attachments must send plain text, never stale vision blocks");
        var text = (string)lastUser.Content!;
        text.Should().Be("now describe what you made");
        text.Should().NotContain(_imagePath, "turn 1's attachment must not leak into turn 2");
    }

    [Fact]
    public async Task Gateway_Turn_With_Attachment_Still_Sends_Vision_Blocks()
    {
        // The D2 rescoping must not break the R9 D4 feature itself: a NORMAL
        // turn with an attached image still builds multimodal content blocks.
        await _vm.AttachFilesAsync(new[] { _imagePath });
        _vm.Conversation = await SeedConversationAsync("c2");

        _vm.InputText = "what is in this picture?";
        await _vm.SendCommand.ExecuteAsync(null);

        _gatewayTurns.Should().HaveCount(1);
        var lastUser = _gatewayTurns[0].Last(m => m.Role == "user");
        lastUser.Content.Should().NotBeOfType<string>(
            "an attached image on a vision-capable model becomes content blocks");
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
