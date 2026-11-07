using System.Text;
using FluentAssertions;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Api;
using Xunit;

namespace SranDesktop.Tests.Services;

public class SseStreamReaderTests
{
    private static async Task<List<SseFrame>> ParseAsync(string raw)
    {
        using var stream = new MemoryStream(Encoding.UTF8.GetBytes(raw));
        var frames = new List<SseFrame>();
        await foreach (var frame in SseStreamReader.ParseAsync(stream))
            frames.Add(frame);
        return frames;
    }

    [Fact]
    public async Task Parses_Named_Events()
    {
        var frames = await ParseAsync(
            "event: message_start\ndata: {\"message_id\":\"m1\"}\n\n" +
            "event: content_delta\ndata: {\"delta\":\"Hello\"}\n\n");

        frames.Should().HaveCount(2);
        frames[0].Should().Be(new SseFrame("message_start", "{\"message_id\":\"m1\"}"));
        frames[1].Should().Be(new SseFrame("content_delta", "{\"delta\":\"Hello\"}"));
    }

    [Fact]
    public async Task Joins_Multiple_Data_Lines_With_Newline()
    {
        var frames = await ParseAsync("data: line1\ndata: line2\n\n");

        frames.Should().ContainSingle()
            .Which.Data.Should().Be("line1\nline2");
    }

    [Fact]
    public async Task Event_Without_Name_Has_Null_Event()
    {
        var frames = await ParseAsync("data: {\"x\":1}\n\n");

        frames.Should().ContainSingle().Which.Event.Should().BeNull();
    }

    [Fact]
    public async Task Ignores_Comments_Id_And_Retry_Lines()
    {
        var frames = await ParseAsync(
            ": keep-alive\nid: 42\nretry: 3000\nevent: e\ndata: d\n\n");

        frames.Should().ContainSingle().Which.Should().Be(new SseFrame("e", "d"));
    }

    [Fact]
    public async Task Flushes_Trailing_Frame_Without_Blank_Line()
    {
        var frames = await ParseAsync("event: done\ndata: bye");

        frames.Should().ContainSingle().Which.Should().Be(new SseFrame("done", "bye"));
    }

    [Fact]
    public async Task Handles_CRLF_Line_Endings()
    {
        var frames = await ParseAsync("event: e\r\ndata: d\r\n\r\n");

        frames.Should().ContainSingle().Which.Should().Be(new SseFrame("e", "d"));
    }

    [Fact]
    public async Task Blank_Line_Without_Data_Emits_Nothing()
    {
        var frames = await ParseAsync("\n\n\n");

        frames.Should().BeEmpty();
    }

    [Fact]
    public async Task Preserves_Utf8_Thai_Text()
    {
        var frames = await ParseAsync("event: content_delta\ndata: {\"delta\":\"สวัสดีครับ\"}\n\n");

        frames.Should().ContainSingle().Which.Data.Should().Contain("สวัสดีครับ");
    }
}
