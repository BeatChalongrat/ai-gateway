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
/// Phase 14 A: attaching a file records it in the conversation's durable file
/// manifest, and the never-trimmed system prompt lists those files so the model
/// can read_file them long after their turn scrolled out of the context window.
/// </summary>
public class ChatViewModelFileManifestTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly ChatViewModel _vm;
    private readonly List<List<GatewayMessage>> _gatewayTurns = new();

    public ChatViewModelFileManifestTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-manifest-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_dbDir);
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));

        var skills = new Mock<ISkillManager>();
        skills.Setup(s => s.FindMatchingSkillsAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Array.Empty<Skill>());

        var orchestrator = new Mock<IChatOrchestrator>();
        orchestrator.Setup(o => o.RunAsync(
                It.IsAny<string>(), It.IsAny<List<GatewayMessage>>(), It.IsAny<string>(),
                It.IsAny<int?>(), It.IsAny<CancellationToken>()))
            // Snapshot the system prompt at call time (it is rebuilt per turn).
            .Callback((string _, List<GatewayMessage> messages, string _, int? _, CancellationToken _) =>
                _gatewayTurns.Add(messages.Select(m => new GatewayMessage
                {
                    Role = m.Role, Content = m.Content, ToolCalls = m.ToolCalls,
                    ToolCallId = m.ToolCallId, Name = m.Name
                }).ToList()))
            .Returns(DoneOnly());

        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());

        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ListMessagesAsync(
                It.IsAny<string>(), It.IsAny<int>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MessageListResponse(false, new List<MessageDto>()));

        // Files attached while a conversation is open route through the file
        // service; return a ready dto so the pending chip flips to "ready".
        var fileService = new Mock<IFileService>();
        fileService.Setup(f => f.UploadAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<IProgress<double>?>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync((string _, string path, IProgress<double>? _, CancellationToken _) =>
                new FileAttachmentDto($"srv-{Guid.NewGuid():N}", "c1", Path.GetFileName(path),
                    "text/plain", 0, null, "done", DateTimeOffset.UtcNow));

        _vm = new ChatViewModel(
            api.Object,
            Mock.Of<IGatewayApiClient>(),
            _store,
            fileService.Object,
            orchestrator.Object,
            Mock.Of<IChatService>(),
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
            Title = "My chat", // titled → no title-generation round-trip
            ModelId = "gpt-4o-mini",
            CreatedAt = DateTimeOffset.UtcNow.AddMinutes(-5),
            UpdatedAt = DateTimeOffset.UtcNow,
            Source = ConversationSource.Local
        };
        await _store.UpsertConversationAsync(entity);
        return new ConversationItemViewModel(entity);
    }

    private string WriteFile(string name, string content = "data")
    {
        var path = Path.Combine(_dbDir, name);
        File.WriteAllText(path, content);
        return path;
    }

    private string LastSystemPrompt() => (string)_gatewayTurns[^1][0].Content!;

    [Fact]
    public async Task Attaching_Two_Files_Records_Both_Rows_And_Lists_Them_In_System_Prompt()
    {
        var a = WriteFile("alpha.txt", "hello");
        var b = WriteFile("beta.csv", "x,y,z");
        await _vm.AttachFilesAsync(new[] { a, b });   // no conversation yet → "ready"
        _vm.Conversation = await SeedConversationAsync();

        _vm.InputText = "use these";
        await _vm.SendCommand.ExecuteAsync(null);

        var rows = await _store.GetFilesAsync("c1");
        rows.Should().HaveCount(2);
        rows.Select(r => r.Name).Should().BeEquivalentTo(new[] { "alpha.txt", "beta.csv" });
        rows.Should().OnlyContain(r => r.LocalPath != null);

        var prompt = LastSystemPrompt();
        prompt.Should().Contain("## Files in this conversation");
        prompt.Should().Contain("alpha.txt");
        prompt.Should().Contain("beta.csv");
        prompt.Should().Contain(a);
        prompt.Should().Contain("read_file"); // the affordance to re-open a file
    }

    [Fact]
    public async Task Re_Sending_The_Same_File_Does_Not_Duplicate_Its_Row()
    {
        var a = WriteFile("alpha.txt");
        await _vm.AttachFilesAsync(new[] { a });
        _vm.Conversation = await SeedConversationAsync();

        _vm.InputText = "first";
        await _vm.SendCommand.ExecuteAsync(null);

        // Attach the SAME file again in a second turn (conversation now open).
        await _vm.AttachFilesAsync(new[] { a });
        _vm.InputText = "again";
        await _vm.SendCommand.ExecuteAsync(null);

        var rows = await _store.GetFilesAsync("c1");
        rows.Should().ContainSingle(r => r.LocalPath == a);
    }

    [Fact]
    public async Task Manifest_Block_Is_Capped_At_Twenty_With_Overflow_Note()
    {
        var paths = Enumerable.Range(0, 25).Select(i => WriteFile($"file{i:00}.txt")).ToArray();
        await _vm.AttachFilesAsync(paths);
        _vm.Conversation = await SeedConversationAsync();

        _vm.InputText = "lots of files";
        await _vm.SendCommand.ExecuteAsync(null);

        (await _store.GetFilesAsync("c1")).Should().HaveCount(25);

        var prompt = LastSystemPrompt();
        // The block itself lists at most 20 "- name" lines.
        var listedLines = prompt.Split('\n').Count(l => l.StartsWith("- file"));
        listedLines.Should().Be(20);
        prompt.Should().Contain("(+5 more)");
    }

    [Fact]
    public async Task Conversation_With_No_Files_Has_No_Manifest_Block()
    {
        _vm.Conversation = await SeedConversationAsync();

        _vm.InputText = "just chatting";
        await _vm.SendCommand.ExecuteAsync(null);

        LastSystemPrompt().Should().NotContain("## Files in this conversation");
    }

    // FIX 7 — the base system prompt (never trimmed) must carry the
    // respond-in-the-user's-language instruction (Part A, the P0) and the
    // act-with-tools proactivity instruction (Part B).
    [Fact]
    public async Task System_Prompt_Includes_Language_And_Proactivity_Instructions()
    {
        _vm.Conversation = await SeedConversationAsync();
        _vm.InputText = "hi";
        await _vm.SendCommand.ExecuteAsync(null);

        var prompt = LastSystemPrompt();
        // Part A — respond in the user's language. (Single-line substrings:
        // the prompt hard-wraps, so phrases must not span a line break.)
        prompt.Should().Contain("SAME language the user writes in");
        prompt.Should().Contain("Match the user's language every turn");
        // Part B — act with tools, don't just describe.
        prompt.Should().Contain("do NOT just describe the command");
        prompt.Should().Contain("Acting is the default");
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
