using System.Net.Http;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.Entities;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>
/// Option 1: a scanned PDF has no text layer, so read_document sends it to
/// chat-edge's OCR (the only Thai-capable one in the product) and waits for the
/// result. Extraction is asynchronous server-side — the upload returns long
/// before the text exists — so the polling here is load-bearing, not a retry.
/// </summary>
public sealed class ScannedDocumentOcrServiceTests : IDisposable
{
    private readonly string _dir;
    private readonly string _scan;
    private readonly Mock<IFileService> _files = new();
    private readonly Mock<IChatEdgeApiClient> _api = new();
    private readonly Mock<IConversationStore> _store = new();
    private readonly ShellSession _session = new();

    public ScannedDocumentOcrServiceTests()
    {
        _dir = Path.Combine(Path.GetTempPath(), $"sran-ocr-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_dir);
        _scan = Path.Combine(_dir, "scan.pdf");
        File.WriteAllBytes(_scan, new byte[] { 1, 2, 3, 4 });

        _files.Setup(f => f.UploadAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<IProgress<double>?>(),
                It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(Attachment("file-1"));

        _session.CurrentConversationId = "local-1";
        _store.Setup(s => s.GetConversationAsync("local-1", It.IsAny<CancellationToken>()))
              .ReturnsAsync(Conversation("local-1", serverId: null));
    }

    public void Dispose()
    {
        try { Directory.Delete(_dir, recursive: true); } catch (IOException) { }
    }

    private static FileAttachmentDto Attachment(string id) =>
        new(id, "conv", "scan.pdf", "application/pdf", 4, null, "processing", DateTimeOffset.Now);

    private static Conversation Conversation(string id, string? serverId) =>
        new() { Id = id, CreatedAt = DateTimeOffset.Now, ServerId = serverId };

    private ScannedDocumentOcrService Build() =>
        new(_files.Object, _api.Object, _store.Object, _session,
            NullLogger<ScannedDocumentOcrService>.Instance,
            pollInterval: TimeSpan.FromMilliseconds(1),
            timeout: TimeSpan.FromSeconds(5));

    private void QueueStatuses(params FileTextDto[] responses)
    {
        var queue = new Queue<FileTextDto>(responses);
        _api.Setup(a => a.GetFileTextAsync("file-1", It.IsAny<CancellationToken>()))
            .ReturnsAsync(() => queue.Count > 1 ? queue.Dequeue() : queue.Peek());
    }

    [Fact]
    public async Task Waits_For_The_Server_To_Finish_Extracting()
    {
        QueueStatuses(
            new FileTextDto("pending", null, false, false),
            new FileTextDto("processing", null, false, false),
            new FileTextDto("completed", "[OCR] สวัสดี", false, true));

        var result = await Build().ExtractAsync(_scan);

        result.Success.Should().BeTrue(result.Error);
        result.Text.Should().Be("[OCR] สวัสดี");
        // Three polls: the first two statuses must not be mistaken for "done".
        _api.Verify(a => a.GetFileTextAsync("file-1", It.IsAny<CancellationToken>()), Times.Exactly(3));
    }

    [Fact]
    public async Task Reports_Server_Side_Truncation()
    {
        QueueStatuses(new FileTextDto("completed", "first pages only", Truncated: true, Ocr: true));

        var result = await Build().ExtractAsync(_scan);

        result.Truncated.Should().BeTrue("the caller must not present a capped read as the whole document");
    }

    [Fact]
    public async Task Uploads_Into_The_Server_Mirror_When_The_Conversation_Has_One()
    {
        _store.Setup(s => s.GetConversationAsync("local-1", It.IsAny<CancellationToken>()))
              .ReturnsAsync(Conversation("local-1", serverId: "server-9"));
        QueueStatuses(new FileTextDto("completed", "text", false, true));

        await Build().ExtractAsync(_scan);

        _files.Verify(f => f.UploadAsync("server-9", _scan, It.IsAny<IProgress<double>?>(),
            It.IsAny<string?>(), It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task Sends_A_Stable_Dedup_Key_So_Rereading_A_File_Does_Not_Re_Ocr_It()
    {
        QueueStatuses(new FileTextDto("completed", "text", false, true));
        var keys = new List<string?>();
        _files.Setup(f => f.UploadAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<IProgress<double>?>(),
                It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .Callback((string _, string _, IProgress<double>? _, string? clientId, CancellationToken _) =>
                keys.Add(clientId))
            .ReturnsAsync(Attachment("file-1"));

        var service = Build();
        await service.ExtractAsync(_scan);
        await service.ExtractAsync(_scan);

        keys.Should().HaveCount(2);
        keys[0].Should().NotBeNullOrEmpty().And.Be(keys[1]);
    }

    [Fact]
    public async Task A_Changed_File_Gets_A_Different_Dedup_Key()
    {
        QueueStatuses(new FileTextDto("completed", "text", false, true));
        var keys = new List<string?>();
        _files.Setup(f => f.UploadAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<IProgress<double>?>(),
                It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .Callback((string _, string _, IProgress<double>? _, string? clientId, CancellationToken _) =>
                keys.Add(clientId))
            .ReturnsAsync(Attachment("file-1"));

        var service = Build();
        await service.ExtractAsync(_scan);
        File.WriteAllBytes(_scan, new byte[] { 9, 9, 9, 9, 9, 9 });
        await service.ExtractAsync(_scan);

        keys[0].Should().NotBe(keys[1], "an edited file must be re-read, not served from the old row");
    }

    [Fact]
    public async Task A_Failed_Extraction_Stops_Instead_Of_Polling_Forever()
    {
        QueueStatuses(new FileTextDto("failed", null, false, false));

        var result = await Build().ExtractAsync(_scan);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("scan.pdf").And.Contain("failed");
    }

    [Fact]
    public async Task Completed_But_Empty_Says_So_Instead_Of_Returning_Nothing()
    {
        // An empty string would read to the model as "this document is blank".
        QueueStatuses(new FileTextDto("completed", "   ", false, true));

        var result = await Build().ExtractAsync(_scan);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("no readable text");
    }

    [Fact]
    public async Task Offline_Upload_Explains_That_Scans_Need_The_Server()
    {
        _files.Setup(f => f.UploadAsync(
                It.IsAny<string>(), It.IsAny<string>(), It.IsAny<IProgress<double>?>(),
                It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("No such host is known"));

        var result = await Build().ExtractAsync(_scan);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("connection").And.Contain("offline");
    }

    [Fact]
    public async Task A_Server_That_Dies_Mid_Poll_Fails_Fast()
    {
        _api.Setup(a => a.GetFileTextAsync("file-1", It.IsAny<CancellationToken>()))
            .ThrowsAsync(new HttpRequestException("connection reset"));

        var result = await Build().ExtractAsync(_scan);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("connection");
    }

    [Fact]
    public async Task Without_An_Active_Conversation_It_Never_Uploads()
    {
        _session.CurrentConversationId = null;

        var result = await Build().ExtractAsync(_scan);

        result.Success.Should().BeFalse();
        _files.Verify(f => f.UploadAsync(It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<IProgress<double>?>(), It.IsAny<string?>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Gives_Up_At_The_Timeout_Rather_Than_Hanging_The_Turn()
    {
        QueueStatuses(new FileTextDto("processing", null, false, false));
        var service = new ScannedDocumentOcrService(
            _files.Object, _api.Object, _store.Object, _session,
            NullLogger<ScannedDocumentOcrService>.Instance,
            pollInterval: TimeSpan.FromMilliseconds(1),
            timeout: TimeSpan.FromMilliseconds(30));

        var result = await service.ExtractAsync(_scan);

        result.Success.Should().BeFalse();
        result.Error.Should().Contain("Timed out");
    }

    [Fact]
    public async Task Cancelling_The_Turn_Cancels_The_Wait()
    {
        QueueStatuses(new FileTextDto("processing", null, false, false));
        using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(50));

        var act = async () => await Build().ExtractAsync(_scan, cts.Token);

        await act.Should().ThrowAsync<OperationCanceledException>();
    }
}
