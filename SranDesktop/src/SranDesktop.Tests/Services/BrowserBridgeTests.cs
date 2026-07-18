using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Models;
using SranDesktop.Infrastructure.Native.Browser;
using SranDesktop.Infrastructure.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

/// <summary>Browser bridge wire format: frame codec round-trip + limits.</summary>
public class BrowserWireTests
{
    [Fact]
    public async Task Frame_RoundTrips_Through_The_Codec()
    {
        using var stream = new MemoryStream();
        var payload = Encoding.UTF8.GetBytes("""{"id":1,"method":"tabs.list"}""");

        await BrowserWire.WriteFrameAsync(stream, payload);
        stream.Position = 0;
        var read = await BrowserWire.ReadFrameAsync(stream);

        read.Should().Equal(payload);
    }

    [Fact]
    public async Task Multiple_Frames_Read_Back_In_Order()
    {
        using var stream = new MemoryStream();
        await BrowserWire.WriteFrameAsync(stream, Encoding.UTF8.GetBytes("one"));
        await BrowserWire.WriteFrameAsync(stream, Encoding.UTF8.GetBytes("two"));
        stream.Position = 0;

        Encoding.UTF8.GetString((await BrowserWire.ReadFrameAsync(stream))!).Should().Be("one");
        Encoding.UTF8.GetString((await BrowserWire.ReadFrameAsync(stream))!).Should().Be("two");
        (await BrowserWire.ReadFrameAsync(stream)).Should().BeNull(); // clean EOF
    }

    [Fact]
    public async Task Oversized_Write_And_Read_Are_Rejected()
    {
        using var stream = new MemoryStream();
        var tooBig = new byte[BrowserWire.MaxMessageBytes + 1];
        var write = () => BrowserWire.WriteFrameAsync(stream, tooBig);
        await write.Should().ThrowAsync<InvalidDataException>();

        // A hostile length header must not allocate/hang either.
        using var bad = new MemoryStream(new byte[] { 0xFF, 0xFF, 0xFF, 0x7F, 0x00 });
        var read = () => BrowserWire.ReadFrameAsync(bad);
        await read.Should().ThrowAsync<InvalidDataException>();
    }
}

/// <summary>BrowserBridgeService over a REAL named pipe: correlation, timeout,
/// pairing rejection, and the not-connected error.</summary>
public sealed class BrowserBridgeServiceTests : IAsyncDisposable
{
    private const string Token = "test-pairing-token";

    private readonly string _pipeName = $"sran-test-{Guid.NewGuid():N}";
    private readonly BrowserBridgeService _service;

    public BrowserBridgeServiceTests()
    {
        var credentials = new Mock<ICredentialStore>();
        credentials.Setup(c => c.Load("browser:pairing-token")).Returns(Token);
        _service = new BrowserBridgeService(
            credentials.Object, NullLogger<BrowserBridgeService>.Instance,
            pipeName: _pipeName, defaultTimeout: TimeSpan.FromSeconds(5));
        _service.Start();
    }

    private async Task<NamedPipeClientStream> ConnectClientAsync(string token = Token)
    {
        var client = new NamedPipeClientStream(".", _pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        await client.ConnectAsync(5000);
        await Send(client, new { token, hello = true });
        return client;
    }

    private static Task Send(Stream stream, object message) =>
        BrowserWire.WriteFrameAsync(stream, JsonSerializer.SerializeToUtf8Bytes(message));

    private static async Task<JsonDocument> Receive(Stream stream)
    {
        var frame = await BrowserWire.ReadFrameAsync(stream)
            ?? throw new IOException("pipe closed");
        return JsonDocument.Parse(frame);
    }

    private static async Task WaitFor(Func<bool> condition, int timeoutMs = 5000)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
        while (!condition() && DateTime.UtcNow < deadline)
            await Task.Delay(25);
        condition().Should().BeTrue();
    }

    [Fact]
    public async Task Request_Without_A_Client_Reports_Not_Connected()
    {
        var act = () => _service.RequestAsync("tabs.list");
        await act.Should().ThrowAsync<BrowserNotConnectedException>();
    }

    [Fact]
    public async Task Request_Response_Correlates_By_Id()
    {
        await using var client = await ConnectClientAsync();
        await WaitFor(() => _service.IsConnected);

        var pending = _service.RequestAsync("tabs.list", new { filter = "x" });
        using var request = await Receive(client);
        var id = request.RootElement.GetProperty("id").GetInt64();
        request.RootElement.GetProperty("method").GetString().Should().Be("tabs.list");

        await Send(client, new { token = Token, id, result = new { ok = true } });

        var result = await pending;
        result.GetProperty("ok").ValueKind.Should().Be(JsonValueKind.True);
        _service.LastSeenAt.Should().NotBeNull();
    }

    [Fact]
    public async Task Error_Response_Surfaces_As_BridgeException_With_The_Message()
    {
        await using var client = await ConnectClientAsync();
        await WaitFor(() => _service.IsConnected);

        var pending = _service.RequestAsync("page.click", new { @ref = 3 });
        using var request = await Receive(client);
        var id = request.RootElement.GetProperty("id").GetInt64();
        await Send(client, new { token = Token, id, error = "refs are stale — call browser_read_page again" });

        var act = () => pending;
        (await act.Should().ThrowAsync<BrowserBridgeException>())
            .WithMessage("*refs are stale*");
    }

    [Fact]
    public async Task Silent_Extension_Times_Out()
    {
        await using var client = await ConnectClientAsync();
        await WaitFor(() => _service.IsConnected);

        var act = () => _service.RequestAsync("tabs.list", timeout: TimeSpan.FromMilliseconds(300));
        await act.Should().ThrowAsync<TimeoutException>();
    }

    [Fact]
    public async Task Wrong_Pairing_Token_Never_Pairs_And_Gets_Dropped()
    {
        await using var client = await ConnectClientAsync(token: "wrong-token");

        // The service must drop the connection without ever reporting connected.
        await Task.Delay(300);
        _service.IsConnected.Should().BeFalse();

        var act = () => _service.RequestAsync("tabs.list");
        await act.Should().ThrowAsync<BrowserNotConnectedException>();
    }

    [Fact]
    public async Task Reconnect_After_Disconnect_Works()
    {
        var first = await ConnectClientAsync();
        await WaitFor(() => _service.IsConnected);
        await first.DisposeAsync();
        await WaitFor(() => !_service.IsConnected);

        await using var second = await ConnectClientAsync();
        await WaitFor(() => _service.IsConnected);
    }

    public async ValueTask DisposeAsync() => await _service.DisposeAsync();
}
