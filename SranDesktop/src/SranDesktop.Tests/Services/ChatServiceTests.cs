using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Events;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Application.Services;
using SranDesktop.Domain.ValueObjects;
using Xunit;

namespace SranDesktop.Tests.Services;

public class ChatServiceTests
{
    private static async IAsyncEnumerable<SseFrame> Frames(params SseFrame[] frames)
    {
        foreach (var frame in frames)
        {
            yield return frame;
            await Task.Yield();
        }
    }

    private static async Task<List<ChatStreamEvent>> RunAsync(params SseFrame[] frames)
    {
        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.StreamMessageAsync("c1", "hi", null, false, false, It.IsAny<CancellationToken>()))
            .Returns(Frames(frames));

        var service = new ChatService(api.Object, NullLogger<ChatService>.Instance);
        var events = new List<ChatStreamEvent>();
        await foreach (var evt in service.SendMessageAsync("c1", "hi"))
            events.Add(evt);
        return events;
    }

    [Fact]
    public async Task Maps_Full_Turn_To_Typed_Events()
    {
        var events = await RunAsync(
            new SseFrame("message_start", """{"message_id":"m1","conversation_id":"c1"}"""),
            new SseFrame("content_delta", """{"delta":"Hel"}"""),
            new SseFrame("content_delta", """{"delta":"lo"}"""),
            new SseFrame("message_complete", """{"message_id":"m1","token_usage":{"input":10,"output":5}}"""));

        events.Should().HaveCount(4);
        events[0].Should().Be(new StreamStartedEvent("m1"));
        events[1].Should().Be(new ContentDeltaEvent("Hel"));
        events[2].Should().Be(new ContentDeltaEvent("lo"));
        events[3].Should().Be(new StreamCompletedEvent(new TokenUsage(10, 5, 15)));
    }

    [Fact]
    public async Task Maps_Error_Event()
    {
        var events = await RunAsync(
            new SseFrame("error", """{"code":"provider_error","message":"Rate limited"}"""));

        events.Should().ContainSingle()
            .Which.Should().Be(new StreamErrorEvent("provider_error", "Rate limited"));
    }

    [Fact]
    public async Task Maps_Reasoning_And_Status_Events()
    {
        var events = await RunAsync(
            new SseFrame("analysis_status", """{"status":"started"}"""),
            new SseFrame("reasoning_delta", """{"delta":"thinking…"}"""),
            new SseFrame("analysis_status", """{"status":"completed"}"""));

        events.Should().HaveCount(3);
        events[0].Should().Be(new StreamStatusEvent("analysis", "started"));
        events[1].Should().Be(new ReasoningDeltaEvent("thinking…"));
        events[2].Should().Be(new StreamStatusEvent("analysis", "completed"));
    }

    [Fact]
    public async Task Maps_Reasoning_Notice_Event()
    {
        var events = await RunAsync(
            new SseFrame("reasoning_notice",
                """{"model":"gpt-4o-mini","reason":"resolved model is not reasoning-capable"}"""));

        events.Should().ContainSingle().Which.Should().Be(
            new ReasoningNoticeEvent("gpt-4o-mini", "resolved model is not reasoning-capable"));
    }

    [Fact]
    public async Task Skips_Unknown_And_Malformed_Frames()
    {
        var events = await RunAsync(
            new SseFrame("message_sources", """[{"title":"x"}]"""),
            new SseFrame("content_delta", "not json at all"),
            new SseFrame("content_delta", """{"delta":"ok"}"""));

        events.Should().ContainSingle().Which.Should().Be(new ContentDeltaEvent("ok"));
    }

    [Fact]
    public async Task Completion_Without_Usage_Yields_Null_Usage()
    {
        var events = await RunAsync(new SseFrame("message_complete", """{"message_id":"m1"}"""));

        events.Should().ContainSingle().Which.Should().Be(new StreamCompletedEvent(null));
    }

    [Fact]
    public async Task Maps_Message_Files_Event()
    {
        var events = await RunAsync(new SseFrame("message_files",
            """
            {"message_id":"m1","files":[{"id":"f1","filename":"generated_cat.png","content_type":"image/png","file_size_bytes":1234,"download_url":"/files/f1/download"}]}
            """));

        var files = events.Should().ContainSingle()
            .Which.Should().BeOfType<MessageFilesEvent>().Subject.Files;
        files.Should().ContainSingle();
        files[0].Should().Be(new MessageFileInfo(
            "f1", "generated_cat.png", "image/png", 1234, "/files/f1/download"));
    }

    [Fact]
    public async Task ResumeMessageAsync_Maps_The_Same_Event_Shape_As_Send()
    {
        // Phase 5 Task 3: the reattach stream (prefix as one big delta, then
        // live deltas) maps through the identical event vocabulary.
        var api = new Mock<IChatEdgeApiClient>();
        api.Setup(a => a.ResumeMessageStreamAsync("c1", "m1", It.IsAny<CancellationToken>()))
            .Returns(Frames(
                new SseFrame("message_start", """{"message_id":"m1","conversation_id":"c1"}"""),
                new SseFrame("content_delta", """{"delta":"the whole already-generated prefix"}"""),
                new SseFrame("content_delta", """{"delta":" and a live tail"}"""),
                new SseFrame("message_complete", """{"message_id":"m1","token_usage":{"input":7,"output":3}}""")));

        var service = new ChatService(api.Object, NullLogger<ChatService>.Instance);
        var events = new List<ChatStreamEvent>();
        await foreach (var evt in service.ResumeMessageAsync("c1", "m1"))
            events.Add(evt);

        events.Should().Equal(
            new StreamStartedEvent("m1"),
            new ContentDeltaEvent("the whole already-generated prefix"),
            new ContentDeltaEvent(" and a live tail"),
            new StreamCompletedEvent(new TokenUsage(7, 3, 10)));
    }

    [Fact]
    public async Task Maps_Image_And_Video_Status_Events()
    {
        var events = await RunAsync(
            new SseFrame("image_generating", """{"status":"started"}"""),
            new SseFrame("video_generating", """{"status":"completed"}"""));

        events.Should().HaveCount(2);
        events[0].Should().Be(new StreamStatusEvent("image", "started"));
        events[1].Should().Be(new StreamStatusEvent("video", "completed"));
    }
}
