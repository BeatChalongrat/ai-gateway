using System.Net.Http;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;
using SranDesktop.Domain.Enums;
using SranDesktop.Infrastructure.Storage;
using SranDesktop.Presentation.ViewModels;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// FIX 9 Part B: opening a conversation reads the SERVER transcript as the
/// source of truth when online — web-made turns appear on desktop — while the
/// local store keeps supplying the agent tool chain, offline fallback, and a
/// write-through cache of server-origin rows.
/// </summary>
public class ChatViewModelServerReadTests : IDisposable
{
    private readonly string _dbDir;
    private readonly SqliteConversationStore _store;
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly ChatViewModel _vm;

    private static readonly DateTimeOffset T0 = new(2026, 7, 1, 10, 0, 0, TimeSpan.Zero);

    public ChatViewModelServerReadTests()
    {
        _dbDir = Path.Combine(Path.GetTempPath(), $"sran-serverread-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_dbDir);
        _store = new SqliteConversationStore(Path.Combine(_dbDir, "data.db"));

        var settings = new Mock<ISettingsService>();
        settings.SetupGet(s => s.Current).Returns(new UserSettings());

        // Model/KB state probe is fire-and-forget — a transport failure is the
        // quiet path the VM already handles.
        _api.Setup(a => a.GetConversationAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("state probe offline"));

        _vm = new ChatViewModel(
            _api.Object,
            Mock.Of<IGatewayApiClient>(),
            _store,
            Mock.Of<IFileService>(),
            Mock.Of<IChatOrchestrator>(),
            Mock.Of<IDurableMediaTurnService>(),
            Mock.Of<ISkillManager>(),
            Mock.Of<ISpeechService>(),
            settings.Object,
            Mock.Of<IAgentConversationSyncService>(),
            NullLogger<ChatViewModel>.Instance);
    }

    private async Task<ConversationItemViewModel> SeedConversationAsync(string id = "conv-1")
    {
        var entity = new Conversation
        {
            Id = id,
            Title = "My chat",
            ModelId = "gpt-4o-mini",
            CreatedAt = T0.AddMinutes(-5),
            UpdatedAt = T0,
            Source = ConversationSource.Server
        };
        await _store.UpsertConversationAsync(entity);
        return new ConversationItemViewModel(entity);
    }

    private static MessageDto Dto(
        string id, string role, string content, DateTimeOffset createdAt, string? clientId = null,
        List<FileAttachmentDto>? files = null) =>
        new(id, role, content, "completed", null, createdAt, null, clientId, files);

    /// <summary>Smallest valid PNG (1×1 transparent) — decodes to a real
    /// BitmapSource so the hydrated attachment renders as an image.</summary>
    private static byte[] OnePixelPng() => Convert.FromBase64String(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==");

    private void SetupServerMessages(params MessageDto[] messages) =>
        _api.Setup(a => a.ListMessagesAsync(
                "conv-1", It.IsAny<int>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MessageListResponse(false, messages.ToList()));

    [Fact]
    public async Task Web_Made_Turns_Render_From_The_Server_And_Are_Cached_Locally()
    {
        // A conversation continued in web-chat: the desktop has NO local rows.
        var conversation = await SeedConversationAsync();
        SetupServerMessages(
            Dto("s-u1", "user", "hello from web", T0),
            Dto("s-a1", "assistant", "hi! (answered on web)", T0.AddSeconds(5)));

        await _vm.OpenConversationAsync(conversation);

        _vm.Messages.Should().HaveCount(2);
        _vm.Messages[0].Content.Should().Be("hello from web");
        _vm.Messages[1].Content.Should().Be("hi! (answered on web)");
        _vm.Messages.Should().OnlyContain(m => m.IsServerMessage);

        // Cached for offline — with the synced marker so the push sync never
        // mirrors these server-origin rows back as new messages.
        var cached = await _store.GetMessagesAsync("conv-1");
        cached.Should().HaveCount(2);
        cached.Should().OnlyContain(m => m.ServerId == "conv-1");
        (await _store.GetUnsyncedMessagesAsync("conv-1")).Should().BeEmpty();
    }

    [Fact]
    public async Task Imported_Rows_Map_To_Their_Local_Ids_So_Agent_Turns_Never_Duplicate()
    {
        // An agent turn that already landed on the server (FIX 9 Part A). The
        // server row carries client_id = our local message id; the transcript
        // must show ONE bubble per message, keyed by the LOCAL id, with the
        // local tool chain re-attached.
        var conversation = await SeedConversationAsync();
        await _store.UpsertMessagesAsync(new[]
        {
            new Message { Id = "u1", ConversationId = "conv-1", Role = MessageRole.User,
                Content = "open the report", CreatedAt = T0, SortOrder = 0, ServerId = "conv-1" },
            new Message { Id = "a1-tc0", ConversationId = "conv-1", Role = MessageRole.Assistant,
                Content = null, ToolCallsJson = """[{"id":"c1","type":"function","function":{"name":"read_file","arguments":"{}"}}]""",
                CreatedAt = T0.AddSeconds(1), SortOrder = 1 },
            new Message { Id = "a1-tc1", ConversationId = "conv-1", Role = MessageRole.Tool,
                Content = "file contents", ToolCallId = "c1", Name = "read_file",
                CreatedAt = T0.AddSeconds(2), SortOrder = 2 },
            new Message { Id = "a1", ConversationId = "conv-1", Role = MessageRole.Assistant,
                Content = "Here is the report.", CreatedAt = T0.AddSeconds(3), SortOrder = 3, ServerId = "conv-1" },
        });
        SetupServerMessages(
            Dto("s-u1", "user", "open the report", T0, clientId: "u1"),
            Dto("s-a1", "assistant", "Here is the report.", T0.AddSeconds(3), clientId: "a1"));

        await _vm.OpenConversationAsync(conversation);

        _vm.Messages.Should().HaveCount(2, "server copy and local copy are the same turn");
        _vm.Messages[0].Id.Should().Be("u1");
        _vm.Messages[1].Id.Should().Be("a1");
        // Imported rows have no per-message server id — feedback stays local.
        _vm.Messages.Should().OnlyContain(m => !m.IsServerMessage);
        // The agent's working context survives (local-only tool chain).
        _vm.Messages[1].AgentToolChain.Should().NotBeNull();
        _vm.Messages[1].AgentToolChain!.Should().HaveCount(2);
    }

    [Fact]
    public async Task Mixed_Conversation_Merges_Web_Turns_With_Local_Agent_Turns_In_Order()
    {
        var conversation = await SeedConversationAsync();
        // Local: one agent turn, already synced.
        await _store.UpsertMessagesAsync(new[]
        {
            new Message { Id = "u1", ConversationId = "conv-1", Role = MessageRole.User,
                Content = "agent turn", CreatedAt = T0, SortOrder = 0, ServerId = "conv-1" },
            new Message { Id = "a1", ConversationId = "conv-1", Role = MessageRole.Assistant,
                Content = "agent answer", CreatedAt = T0.AddSeconds(3), SortOrder = 1, ServerId = "conv-1" },
        });
        // Server: the agent turn (imported) + a later web turn.
        SetupServerMessages(
            Dto("s-u1", "user", "agent turn", T0, clientId: "u1"),
            Dto("s-a1", "assistant", "agent answer", T0.AddSeconds(3), clientId: "a1"),
            Dto("s-u2", "user", "web follow-up", T0.AddMinutes(1)),
            Dto("s-a2", "assistant", "web answer", T0.AddMinutes(1).AddSeconds(4)));

        await _vm.OpenConversationAsync(conversation);

        _vm.Messages.Select(m => m.Content).Should().ContainInOrder(
            "agent turn", "agent answer", "web follow-up", "web answer");
        _vm.Messages.Should().HaveCount(4);
    }

    private static readonly string ImgId = Guid.NewGuid().ToString();
    private static readonly string DocId = Guid.NewGuid().ToString();

    [Fact]
    public async Task Server_Files_Hydrate_Into_Bubbles_And_Cache_Locally()
    {
        // FIX 10: a conversation with web-uploaded attachments renders them
        // on desktop — image inline in the user bubble, PDF as a file chip.
        // FIX 10b: only the IMAGE downloads on open (it renders inline); the
        // PDF chip is metadata-only until the user saves, and the image's
        // bytes are cached so a re-open works offline with no second download.
        var conversation = await SeedConversationAsync();
        var png = OnePixelPng();
        _api.Setup(a => a.DownloadFileAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string url, CancellationToken _) =>
                url.Contains(ImgId) ? png : "%PDF-fake"u8.ToArray());
        var files = new List<FileAttachmentDto>
        {
            new(ImgId, "conv-1", "photo.png", "image/png", png.Length,
                $"/files/{ImgId}/download", "completed", T0),
            new(DocId, "conv-1", "report.pdf", "application/pdf", 9,
                $"/files/{DocId}/download", "completed", T0),
        };
        SetupServerMessages(Dto("s-u1", "user", "uploaded on web", T0, files: files));

        await _vm.OpenConversationAsync(conversation);
        await _vm.AttachmentHydration;

        var user = _vm.Messages.Should().ContainSingle().Subject;
        user.InlineImages.Should().ContainSingle(a => a.Filename == "photo.png" && a.IsImage);
        user.InlineImages.Single().ImageSource.Should().NotBeNull("the PNG must decode");
        var chip = user.InlineAttachments.Should()
            .ContainSingle(a => a.Filename == "report.pdf" && a.IsDocument).Subject;

        // FIX 10b: exactly ONE download (the image) — the doc chip fetched nothing.
        _api.Verify(a => a.DownloadFileAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Once);
        chip.RawBytes.Should().BeEmpty("documents hydrate metadata-only");
        var rows = await _store.GetFilesAsync("conv-1");
        rows.Should().ContainSingle("only the image was fetched and cached")
            .Which.Should().Match<Domain.Entities.FileAttachment>(r =>
                r.ServerUrl != null && r.LocalPath != null && File.Exists(r.LocalPath));

        // Saving the document triggers the lazy fetch — download fires, the
        // bytes come back, and the offline cache row is written.
        var docBytes = await chip.ResolveBytesAsync();
        docBytes.Should().NotBeNull();
        docBytes!.Should().BeEquivalentTo("%PDF-fake"u8.ToArray());
        _api.Verify(a => a.DownloadFileAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Exactly(2));
        rows = await _store.GetFilesAsync("conv-1");
        rows.Should().HaveCount(2);
        rows.Should().OnlyContain(r =>
            r.ServerUrl != null && r.LocalPath != null && File.Exists(r.LocalPath));

        // A repeat save re-uses the in-hand bytes — no third download.
        (await chip.ResolveBytesAsync()).Should().NotBeNull();
        _api.Verify(a => a.DownloadFileAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Exactly(2));

        // Re-open: the image comes from the local cache — zero new downloads.
        _api.Invocations.Clear();
        await _vm.OpenConversationAsync(conversation);
        await _vm.AttachmentHydration;

        _api.Verify(a => a.DownloadFileAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Never);
        _vm.Messages.Single().InlineImages.Should().ContainSingle();
        _vm.Messages.Single().InlineAttachments.Should().ContainSingle();

        // Tidy the shared file cache (fixed LocalAppData location).
        foreach (var row in await _store.GetFilesAsync("conv-1"))
            try { File.Delete(row.LocalPath!); } catch (IOException) { }
    }

    [Fact]
    public async Task Lazy_Document_Save_Fails_Gracefully_When_Offline()
    {
        // FIX 10b: the chip renders from metadata even when the server is
        // unreachable; resolving bytes for Save then reports failure (null)
        // instead of throwing — SaveAs turns that into a toast.
        var conversation = await SeedConversationAsync();
        _api.Setup(a => a.DownloadFileAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));
        var docId = Guid.NewGuid().ToString();
        SetupServerMessages(Dto("s-u1", "user", "doc while offline", T0, files: new List<FileAttachmentDto>
        {
            new(docId, "conv-1", "notes.pdf", "application/pdf", 9,
                $"/files/{docId}/download", "completed", T0),
        }));

        await _vm.OpenConversationAsync(conversation);
        await _vm.AttachmentHydration;

        var chip = _vm.Messages.Single().InlineAttachments.Should().ContainSingle().Subject;
        (await chip.ResolveBytesAsync()).Should().BeNullOrEmpty("offline fetch reports failure, no throw");
    }

    [Fact]
    public async Task Non_Uuid_File_Id_From_The_Server_Is_Rejected_Before_Any_Filesystem_Use()
    {
        // FIX 10b: file.Id is server-controlled and participates in the
        // filecache path — a traversal-shaped id must be dropped before any
        // download or disk write happens.
        var conversation = await SeedConversationAsync();
        SetupServerMessages(Dto("s-u1", "user", "hostile file id", T0, files: new List<FileAttachmentDto>
        {
            new(@"..\..\..\evil", "conv-1", "innocent.png", "image/png", 4,
                @"/files/..\..\..\evil/download", "completed", T0),
        }));

        await _vm.OpenConversationAsync(conversation);
        await _vm.AttachmentHydration;

        var user = _vm.Messages.Single();
        user.InlineImages.Should().BeEmpty();
        user.InlineAttachments.Should().BeEmpty();
        _api.Verify(a => a.DownloadFileAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()),
            Times.Never);
        (await _store.GetFilesAsync("conv-1")).Should().BeEmpty("nothing may be cached for a rejected id");
    }

    [Fact]
    public async Task Offline_Open_Falls_Back_To_The_Local_Cache()
    {
        var conversation = await SeedConversationAsync();
        await _store.UpsertMessagesAsync(new[]
        {
            new Message { Id = "u1", ConversationId = "conv-1", Role = MessageRole.User,
                Content = "cached question", CreatedAt = T0, SortOrder = 0, ServerId = "conv-1" },
            new Message { Id = "a1", ConversationId = "conv-1", Role = MessageRole.Assistant,
                Content = "cached answer", CreatedAt = T0.AddSeconds(2), SortOrder = 1, ServerId = "conv-1" },
        });
        _api.Setup(a => a.ListMessagesAsync(
                It.IsAny<string>(), It.IsAny<int>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("offline"));

        await _vm.OpenConversationAsync(conversation);

        _vm.Messages.Should().HaveCount(2);
        _vm.Messages[0].Content.Should().Be("cached question");
        _vm.Messages[1].Content.Should().Be("cached answer");
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
